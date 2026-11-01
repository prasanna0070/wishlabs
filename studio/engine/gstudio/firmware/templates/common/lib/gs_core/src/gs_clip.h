// Voice clips at rest (child_voice_clip mode only).
//
// A clip file on the microSD card is a 32-byte clear header followed by the whole WAV file (44-byte
// header + 16-bit PCM) encrypted with AES-256-CTR under a random 256-bit key that lives in the
// device's own NVS partition (gsnvs), never on the card. A card read on a computer shows no RIFF
// header and no PCM. Erasing everything also destroys the key, so copies a FAT delete leaves behind
// cannot be decrypted either. Removed files are first overwritten with zeros.
//
// Header (little-endian):
//   0  "GSC1"        4  version (1)    5..7  zero
//   8  nonce[12]     random per file; counter block i = nonce || big-endian i, i = plain offset / 16
//   20 saved_hour    the powered-on hour counter when the clip was saved (retention)
//   24 plain_bytes   length of the encrypted WAV
//   28 crc32 of bytes 0..27
//
// The AES block function is mbedtls (ESP-IDF on the device; a reference implementation of the same
// API, checked against the NIST SP 800-38A vectors, in the native tests).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include <mbedtls/aes.h>

namespace gs {

constexpr size_t kClipKeyBytes = 32;
constexpr size_t kClipNonceBytes = 12;
constexpr size_t kClipHeaderBytes = 32;

// The few file operations the clip code needs, so the same code runs on the SD card and in tests.
class ByteFile {
 public:
  virtual ~ByteFile() {}
  virtual size_t read(uint8_t* buf, size_t n) = 0;
  virtual size_t write(const uint8_t* buf, size_t n) = 0;
  virtual bool seek(uint32_t pos) = 0;
  virtual uint32_t size() = 0;
  virtual void flush() {}
};

struct ClipHeader {
  uint8_t nonce[kClipNonceBytes] = {0};
  uint32_t saved_hour = 0;
  uint32_t plain_bytes = 0;
};

void clip_header_encode(const ClipHeader& h, uint8_t out[kClipHeaderBytes]);
// False when the bytes are not a valid GSC1 header (wrong magic, version or CRC).
bool clip_header_decode(const uint8_t* in, size_t n, ClipHeader* out);

// AES-256-CTR keystream at any byte offset (encrypt and decrypt are the same operation).
class CtrCipher {
 public:
  CtrCipher();
  ~CtrCipher();
  CtrCipher(const CtrCipher&) = delete;
  CtrCipher& operator=(const CtrCipher&) = delete;
  bool begin(const uint8_t key[kClipKeyBytes], const uint8_t nonce[kClipNonceBytes]);
  void apply(uint32_t offset, uint8_t* buf, size_t n);
  bool ready() const { return ready_; }

 private:
  mbedtls_aes_context ctx_;
  uint8_t nonce_[kClipNonceBytes];
  uint8_t block_[16];
  uint32_t block_index_ = 0xFFFFFFFFu;
  bool ready_ = false;
};

// Streams 16-bit mono PCM into an encrypted WAV clip. begin() writes both headers (sizes 0) so a
// power cut leaves a file the reader rejects; finish() rewrites them with the real sizes.
class ClipWriter {
 public:
  bool begin(ByteFile* f, const uint8_t key[kClipKeyBytes], const uint8_t nonce[kClipNonceBytes],
             uint32_t saved_hour, uint32_t sample_rate);
  bool write_pcm(const int16_t* pcm, size_t samples);
  bool finish();
  uint32_t data_bytes() const { return data_bytes_; }

 private:
  bool put(uint32_t plain_offset, const uint8_t* data, size_t n);

  ByteFile* f_ = nullptr;
  CtrCipher cipher_;
  ClipHeader header_;
  uint32_t rate_ = 0;
  uint32_t data_bytes_ = 0;
  bool ok_ = false;
};

// Reads an encrypted clip (when a key is given) or a plain file through one interface, so the
// player handles story segments and voice clips alike. Offsets are in the plain WAV.
class ClipReader {
 public:
  // key may be null: then only plain files open. Fails for a clip whose header is damaged.
  bool open(ByteFile* f, const uint8_t* key);
  bool encrypted() const { return encrypted_; }
  uint32_t plain_size() const { return plain_size_; }
  uint32_t saved_hour() const { return header_.saved_hour; }
  bool seek(uint32_t plain_offset);
  size_t read(uint8_t* buf, size_t n);

 private:
  ByteFile* f_ = nullptr;
  CtrCipher cipher_;
  ClipHeader header_;
  bool encrypted_ = false;
  uint32_t plain_size_ = 0;
  uint32_t pos_ = 0;
};

// Overwrites every byte of an open file with zeros, then flushes. Returns false on a short write.
bool zero_fill(ByteFile* f);

}  // namespace gs
