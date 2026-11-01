#include "gs_clip.h"

#include <string.h>

#include "gs_audio_math.h"
#include "gs_util.h"

namespace gs {

static const uint8_t kMagic[4] = {'G', 'S', 'C', '1'};

void clip_header_encode(const ClipHeader& h, uint8_t out[kClipHeaderBytes]) {
  memset(out, 0, kClipHeaderBytes);
  ByteWriter w(out, kClipHeaderBytes);
  w.bytes(kMagic, 4);
  w.u8(1);
  w.u8(0);
  w.u8(0);
  w.u8(0);
  w.bytes(h.nonce, kClipNonceBytes);
  w.u32(h.saved_hour);
  w.u32(h.plain_bytes);
  w.u32(crc32(out, 28));
}

bool clip_header_decode(const uint8_t* in, size_t n, ClipHeader* out) {
  if (!in || n < kClipHeaderBytes || memcmp(in, kMagic, 4) != 0 || in[4] != 1) return false;
  ByteReader r(in + 28, 4);
  if (crc32(in, 28) != r.u32()) return false;
  ByteReader h(in + 8, 20);
  ClipHeader c;
  h.bytes(c.nonce, kClipNonceBytes);
  c.saved_hour = h.u32();
  c.plain_bytes = h.u32();
  if (out) *out = c;
  return true;
}

// ------------------------------------------------------------------ cipher

CtrCipher::CtrCipher() { mbedtls_aes_init(&ctx_); }

CtrCipher::~CtrCipher() {
  mbedtls_aes_free(&ctx_);
  memset(block_, 0, sizeof(block_));
}

bool CtrCipher::begin(const uint8_t key[kClipKeyBytes], const uint8_t nonce[kClipNonceBytes]) {
  ready_ = mbedtls_aes_setkey_enc(&ctx_, key, 256) == 0;
  memcpy(nonce_, nonce, kClipNonceBytes);
  block_index_ = 0xFFFFFFFFu;
  return ready_;
}

void CtrCipher::apply(uint32_t offset, uint8_t* buf, size_t n) {
  for (size_t i = 0; i < n; ++i) {
    const uint32_t pos = offset + uint32_t(i);
    const uint32_t bi = pos / 16;
    if (bi != block_index_) {
      uint8_t ctr[16];
      memcpy(ctr, nonce_, kClipNonceBytes);
      ctr[12] = uint8_t(bi >> 24);
      ctr[13] = uint8_t(bi >> 16);
      ctr[14] = uint8_t(bi >> 8);
      ctr[15] = uint8_t(bi);
      mbedtls_aes_crypt_ecb(&ctx_, MBEDTLS_AES_ENCRYPT, ctr, block_);
      block_index_ = bi;
    }
    buf[i] ^= block_[pos % 16];
  }
}

// ------------------------------------------------------------------ writer

bool ClipWriter::put(uint32_t plain_offset, const uint8_t* data, size_t n) {
  uint8_t tmp[128];
  size_t done = 0;
  if (!f_->seek(uint32_t(kClipHeaderBytes) + plain_offset)) return false;
  while (done < n) {
    const size_t k = (n - done) > sizeof(tmp) ? sizeof(tmp) : n - done;
    memcpy(tmp, data + done, k);
    cipher_.apply(plain_offset + uint32_t(done), tmp, k);
    if (f_->write(tmp, k) != k) return false;
    done += k;
  }
  memset(tmp, 0, sizeof(tmp));
  return true;
}

bool ClipWriter::begin(ByteFile* f, const uint8_t key[kClipKeyBytes], const uint8_t nonce[kClipNonceBytes],
                       uint32_t saved_hour, uint32_t sample_rate) {
  f_ = f;
  ok_ = false;
  data_bytes_ = 0;
  rate_ = sample_rate;
  if (!f_ || !cipher_.begin(key, nonce)) return false;
  header_ = ClipHeader();
  memcpy(header_.nonce, nonce, kClipNonceBytes);
  header_.saved_hour = saved_hour;
  header_.plain_bytes = 0;
  uint8_t hdr[kClipHeaderBytes];
  clip_header_encode(header_, hdr);
  if (!f_->seek(0) || f_->write(hdr, sizeof(hdr)) != sizeof(hdr)) return false;
  uint8_t wav[kWavHeaderBytes];
  wav_header(wav, rate_, 16, 1, 0);
  ok_ = put(0, wav, sizeof(wav));
  return ok_;
}

bool ClipWriter::write_pcm(const int16_t* pcm, size_t samples) {
  if (!ok_) return false;
  const size_t n = samples * 2;
  ok_ = put(uint32_t(kWavHeaderBytes) + data_bytes_, reinterpret_cast<const uint8_t*>(pcm), n);
  if (ok_) data_bytes_ += uint32_t(n);
  return ok_;
}

bool ClipWriter::finish() {
  if (!ok_) return false;
  uint8_t wav[kWavHeaderBytes];
  wav_header(wav, rate_, 16, 1, data_bytes_);
  if (!put(0, wav, sizeof(wav))) return false;
  header_.plain_bytes = uint32_t(kWavHeaderBytes) + data_bytes_;
  uint8_t hdr[kClipHeaderBytes];
  clip_header_encode(header_, hdr);
  if (!f_->seek(0) || f_->write(hdr, sizeof(hdr)) != sizeof(hdr)) return false;
  f_->flush();
  return true;
}

// ------------------------------------------------------------------ reader

bool ClipReader::open(ByteFile* f, const uint8_t* key) {
  f_ = f;
  encrypted_ = false;
  pos_ = 0;
  plain_size_ = 0;
  if (!f_ || !f_->seek(0)) return false;
  uint8_t head[kClipHeaderBytes];
  const size_t got = f_->read(head, sizeof(head));
  if (got >= 4 && memcmp(head, kMagic, 4) == 0) {
    if (!key || !clip_header_decode(head, got, &header_)) return false;
    if (header_.plain_bytes == 0 || header_.plain_bytes + kClipHeaderBytes > f_->size()) return false;
    if (!cipher_.begin(key, header_.nonce)) return false;
    encrypted_ = true;
    plain_size_ = header_.plain_bytes;
  } else {
    plain_size_ = f_->size();
  }
  return seek(0);
}

bool ClipReader::seek(uint32_t plain_offset) {
  if (!f_ || plain_offset > plain_size_) return false;
  pos_ = plain_offset;
  return f_->seek(pos_ + (encrypted_ ? uint32_t(kClipHeaderBytes) : 0u));
}

size_t ClipReader::read(uint8_t* buf, size_t n) {
  if (!f_) return 0;
  if (pos_ + n > plain_size_) n = plain_size_ - pos_;
  const size_t got = f_->read(buf, n);
  if (encrypted_) cipher_.apply(pos_, buf, got);
  pos_ += uint32_t(got);
  return got;
}

bool zero_fill(ByteFile* f) {
  if (!f || !f->seek(0)) return false;
  static const uint8_t zeros[256] = {0};
  uint32_t left = f->size();
  while (left) {
    const size_t k = left > sizeof(zeros) ? sizeof(zeros) : left;
    if (f->write(zeros, k) != k) return false;
    left -= uint32_t(k);
  }
  f->flush();
  return true;
}

}  // namespace gs
