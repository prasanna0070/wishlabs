// Shared privacy and security logic: clip encryption, zero-fill, the mic gate, the PIN guard table,
// and the microphone-to-WAV-to-playback sample path (MAJ-1, MAJ-2, MAJ-18, MIN-16).
#include <unity.h>

#include <math.h>
#include <string.h>

#include <string>
#include <vector>

#include "gs_audio_math.h"
#include "gs_clip.h"
#include "gs_micgate.h"
#include "gs_pin.h"

namespace {

struct MemFile : public gs::ByteFile {
  std::vector<uint8_t> data;
  size_t pos = 0;
  size_t read(uint8_t* buf, size_t n) override {
    size_t k = 0;
    while (k < n && pos < data.size()) buf[k++] = data[pos++];
    return k;
  }
  size_t write(const uint8_t* buf, size_t n) override {
    if (pos + n > data.size()) data.resize(pos + n);
    memcpy(data.data() + pos, buf, n);
    pos += n;
    return n;
  }
  bool seek(uint32_t p) override {
    if (p > data.size()) return false;
    pos = p;
    return true;
  }
  uint32_t size() override { return uint32_t(data.size()); }
};

void hex(const char* s, uint8_t* out, size_t n) {
  for (size_t i = 0; i < n; ++i) {
    unsigned v = 0;
    sscanf(s + 2 * i, "%2x", &v);
    out[i] = uint8_t(v);
  }
}

bool contains(const std::vector<uint8_t>& hay, const char* needle) {
  const size_t n = strlen(needle);
  for (size_t i = 0; i + n <= hay.size(); ++i)
    if (memcmp(hay.data() + i, needle, n) == 0) return true;
  return false;
}

const char* kKeyHex = "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4";

}  // namespace

static void test_aes256_block_matches_nist_vectors() {
  // NIST SP 800-38A F.1.5 (ECB-AES256 block 1) and F.5.5 (CTR-AES256 block 1).
  uint8_t key[32], pt[16], ct[16], expect[16];
  hex(kKeyHex, key, 32);
  hex("6bc1bee22e409f96e93d7e117393172a", pt, 16);
  mbedtls_aes_context c;
  mbedtls_aes_init(&c);
  TEST_ASSERT_EQUAL(0, mbedtls_aes_setkey_enc(&c, key, 256));
  mbedtls_aes_crypt_ecb(&c, MBEDTLS_AES_ENCRYPT, pt, ct);
  hex("f3eed1bdb5d2a03c064b5a7e3db181f8", expect, 16);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expect, ct, 16);
  uint8_t ctr[16], ks[16];
  hex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff", ctr, 16);
  mbedtls_aes_crypt_ecb(&c, MBEDTLS_AES_ENCRYPT, ctr, ks);
  for (int i = 0; i < 16; ++i) ct[i] = uint8_t(pt[i] ^ ks[i]);
  hex("601ec313775789a5b7a7f504bbf3d228", expect, 16);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expect, ct, 16);
  mbedtls_aes_free(&c);
}

static void test_ctr_cipher_counter_layout_and_offsets() {
  uint8_t key[32], nonce[12], block[16], ks[16];
  hex(kKeyHex, key, 32);
  for (int i = 0; i < 12; ++i) nonce[i] = uint8_t(0xA0 + i);
  // Keystream block 2 = AES(nonce || 00000002).
  memcpy(block, nonce, 12);
  block[12] = block[13] = block[14] = 0;
  block[15] = 2;
  mbedtls_aes_context c;
  mbedtls_aes_init(&c);
  mbedtls_aes_setkey_enc(&c, key, 256);
  mbedtls_aes_crypt_ecb(&c, MBEDTLS_AES_ENCRYPT, block, ks);
  mbedtls_aes_free(&c);
  gs::CtrCipher x;
  TEST_ASSERT_TRUE(x.begin(key, nonce));
  uint8_t buf[16] = {0};
  x.apply(32, buf, 16);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(ks, buf, 16);
  // Any split of the same range gives the same bytes (random access for seek and header rewrite).
  uint8_t a[40] = {0}, b[40] = {0};
  x.apply(5, a, 40);
  x.apply(5, b, 7);
  x.apply(12, b + 7, 33);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(a, b, 40);
}

static std::vector<int16_t> sine(size_t n, float amp) {
  std::vector<int16_t> s(n);
  for (size_t i = 0; i < n; ++i) s[i] = int16_t(amp * sinf(6.2831853f * 440.0f * float(i) / 16000.0f));
  return s;
}

static void test_clip_on_card_is_not_plain_pcm_and_reads_back() {
  // MAJ-1: what lands on the card has no RIFF header and no PCM in clear; with the key it reads back.
  uint8_t key[32], nonce[12];
  hex(kKeyHex, key, 32);
  memset(nonce, 7, sizeof(nonce));
  MemFile f;
  gs::ClipWriter w;
  TEST_ASSERT_TRUE(w.begin(&f, key, nonce, 1234, 16000));
  const std::vector<int16_t> pcm = sine(16000, 9000.0f);
  for (size_t i = 0; i < pcm.size(); i += 160) TEST_ASSERT_TRUE(w.write_pcm(pcm.data() + i, 160));
  TEST_ASSERT_TRUE(w.finish());
  TEST_ASSERT_EQUAL_UINT32(32 + 44 + 32000, f.size());
  TEST_ASSERT_FALSE(contains(f.data, "RIFF"));
  TEST_ASSERT_FALSE(contains(f.data, "WAVE"));
  TEST_ASSERT_FALSE(contains(f.data, "data"));
  // No 32-byte run of the clear PCM appears anywhere in the file.
  std::vector<uint8_t> clear(reinterpret_cast<const uint8_t*>(pcm.data()) + 2000,
                             reinterpret_cast<const uint8_t*>(pcm.data()) + 2032);
  bool found = false;
  for (size_t i = 0; i + 32 <= f.data.size() && !found; ++i) found = memcmp(f.data.data() + i, clear.data(), 32) == 0;
  TEST_ASSERT_FALSE(found);

  gs::ClipReader r;
  TEST_ASSERT_TRUE(r.open(&f, key));
  TEST_ASSERT_TRUE(r.encrypted());
  TEST_ASSERT_EQUAL_UINT32(1234, r.saved_hour());
  uint8_t head[64];
  TEST_ASSERT_EQUAL(64, r.read(head, 64));
  gs::WavInfo wi;
  TEST_ASSERT_TRUE(gs::wav_parse(head, 64, r.plain_size(), &wi));
  TEST_ASSERT_EQUAL_UINT32(16000, wi.sample_rate);
  TEST_ASSERT_EQUAL_UINT32(32000, wi.data_bytes);
  TEST_ASSERT_TRUE(r.seek(wi.data_offset + 2000));
  uint8_t back[32];
  TEST_ASSERT_EQUAL(32, r.read(back, 32));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(clear.data(), back, 32);

  gs::ClipReader nokey;
  TEST_ASSERT_FALSE(nokey.open(&f, nullptr));  // without the device key the clip does not open at all
  uint8_t wrong[32];
  memcpy(wrong, key, 32);
  wrong[0] ^= 1;
  gs::ClipReader bad;
  TEST_ASSERT_TRUE(bad.open(&f, wrong));
  TEST_ASSERT_EQUAL(64, bad.read(head, 64));
  TEST_ASSERT_FALSE(gs::wav_parse(head, 64, bad.plain_size(), &wi));
}

static void test_damaged_clip_header_is_rejected_and_plain_files_pass_through() {
  uint8_t key[32], nonce[12] = {0};
  hex(kKeyHex, key, 32);
  MemFile f;
  gs::ClipWriter w;
  w.begin(&f, key, nonce, 0, 16000);  // power cut before finish(): sizes still 0
  gs::ClipReader r;
  TEST_ASSERT_FALSE(r.open(&f, key));
  MemFile plain;
  uint8_t wav[44];
  gs::wav_header(wav, 24000, 16, 1, 0);
  plain.write(wav, 44);
  gs::ClipReader p;
  TEST_ASSERT_TRUE(p.open(&plain, key));
  TEST_ASSERT_FALSE(p.encrypted());
  TEST_ASSERT_EQUAL_UINT32(44, p.plain_size());
}

static void test_zero_fill_overwrites_every_byte() {
  MemFile f;
  for (int i = 0; i < 1000; ++i) f.data.push_back(uint8_t(i * 7 + 1));
  TEST_ASSERT_TRUE(gs::zero_fill(&f));
  TEST_ASSERT_EQUAL(1000, f.data.size());
  for (uint8_t b : f.data) TEST_ASSERT_EQUAL_HEX8(0, b);
}

struct GateLog {
  std::string events;
};
static bool gate_driver(bool on, void* ctx) {
  static_cast<GateLog*>(ctx)->events += on ? "D1" : "D0";
  return true;
}
static void gate_indicator(bool on, void* ctx) { static_cast<GateLog*>(ctx)->events += on ? "L1" : "L0"; }

static void test_mic_gate_lights_before_open_and_clears_after_close() {
  GateLog log;
  gs::MicGate g;
  g.attach_driver(gate_driver, &log);
  g.attach_indicator(gate_indicator, &log);
  TEST_ASSERT_TRUE(g.set(true));
  TEST_ASSERT_TRUE(g.set(true));  // no change, no events
  TEST_ASSERT_TRUE(g.set(false));
  TEST_ASSERT_EQUAL_STRING("L1D1D0L0", log.events.c_str());
  TEST_ASSERT_EQUAL_UINT32(1, g.opens());
}

static bool failing_driver(bool, void*) { return false; }

static void test_mic_gate_failed_open_clears_the_light() {
  GateLog log;
  gs::MicGate g;
  g.attach_driver(failing_driver, nullptr);
  g.attach_indicator(gate_indicator, &log);
  TEST_ASSERT_FALSE(g.set(true));
  TEST_ASSERT_FALSE(g.is_on());
  TEST_ASSERT_EQUAL_STRING("L1L0", log.events.c_str());
}

static void test_pin_guard_table_is_per_address_with_a_device_cap() {
  gs::PinGuardTable t;
  const uint32_t attacker = 0x0A00002A, parent = 0x0A000005;
  for (int i = 0; i < 5; ++i) t.failure(attacker, 1000);
  TEST_ASSERT_TRUE(t.locked(attacker, 1000));
  TEST_ASSERT_FALSE(t.locked(parent, 1000));  // tripping the lock from one address does not lock the parent out
  // Many addresses: the device-wide guard locks after 20 wrong PINs in total.
  for (uint32_t ip = 100; ip < 115; ++ip) t.failure(ip, 2000);
  TEST_ASSERT_TRUE(t.locked(parent, 2000));
}

static void test_pin_lockout_survives_a_restart() {
  gs::PinGuardTable t;
  for (int i = 0; i < 5; ++i) t.failure(42, 5000);
  TEST_ASSERT_TRUE(t.dirty());
  uint8_t blob[gs::PinGuardTable::kBlobBytes];
  const size_t n = t.save(blob, sizeof(blob), 20000);  // 45 s of the 60 s lock left
  TEST_ASSERT_EQUAL(gs::PinGuardTable::kBlobBytes, n);
  gs::PinGuardTable after;  // a restart: millis() starts again from 0
  TEST_ASSERT_TRUE(after.load(blob, n, 100));
  TEST_ASSERT_TRUE(after.locked(42, 100));
  TEST_ASSERT_EQUAL_UINT32(45000, after.retry_after_ms(42, 100));
  TEST_ASSERT_FALSE(after.locked(42, 45100));
  blob[3] ^= 0x55;  // damaged blob: ignored, not trusted
  gs::PinGuardTable bad;
  TEST_ASSERT_FALSE(bad.load(blob, n, 0));
}

static void test_mic_frames_to_wav_to_playback_gain() {
  // MAJ-18 mic path: synthetic INMP441 frames (24-bit, left-justified in 32-bit slots) ->
  // mic_to_pcm16 -> wav_header -> wav_parse -> playback gain.
  const size_t n = 1600;
  std::vector<int16_t> pcm(n);
  for (size_t i = 0; i < n; ++i) {
    const int32_t s24 = int32_t(0x0FFFFF * sinf(6.2831853f * 500.0f * float(i) / 16000.0f));  // about -18 dBFS
    const int32_t raw = int32_t(uint32_t(s24) << 8);
    pcm[i] = gs::mic_to_pcm16(raw, 13);
  }
  TEST_ASSERT_TRUE(gs::peak_abs(pcm.data(), n) > 30000);  // shift 13 = 8x gain over the top 16 bits: near full scale
  std::vector<uint8_t> file(44 + n * 2);
  gs::wav_header(file.data(), 16000, 16, 1, uint32_t(n * 2));
  memcpy(file.data() + 44, pcm.data(), n * 2);
  gs::WavInfo wi;
  TEST_ASSERT_TRUE(gs::wav_parse(file.data(), file.size(), uint32_t(file.size()), &wi));
  TEST_ASSERT_EQUAL_UINT32(16000, wi.sample_rate);
  TEST_ASSERT_EQUAL(1, wi.channels);
  TEST_ASSERT_EQUAL(16, wi.bits);
  TEST_ASSERT_EQUAL_UINT32(n * 2, wi.data_bytes);
  std::vector<int16_t> play(n);
  memcpy(play.data(), file.data() + wi.data_offset, n * 2);
  const uint16_t g = gs::volume_gain_q12(30, 60);
  gs::apply_gain(play.data(), n, g);
  const float before = gs::rms_dbfs(pcm.data(), n);
  const float after = gs::rms_dbfs(play.data(), n);
  TEST_ASSERT_FLOAT_WITHIN(0.3f, 20.0f * log10f(float(g) / 4096.0f), after - before);
}

void register_privacy_tests() {
  RUN_TEST(test_aes256_block_matches_nist_vectors);
  RUN_TEST(test_ctr_cipher_counter_layout_and_offsets);
  RUN_TEST(test_clip_on_card_is_not_plain_pcm_and_reads_back);
  RUN_TEST(test_damaged_clip_header_is_rejected_and_plain_files_pass_through);
  RUN_TEST(test_zero_fill_overwrites_every_byte);
  RUN_TEST(test_mic_gate_lights_before_open_and_clears_after_close);
  RUN_TEST(test_mic_gate_failed_open_clears_the_light);
  RUN_TEST(test_pin_guard_table_is_per_address_with_a_device_cap);
  RUN_TEST(test_pin_lockout_survives_a_restart);
  RUN_TEST(test_mic_frames_to_wav_to_playback_gain);
}
