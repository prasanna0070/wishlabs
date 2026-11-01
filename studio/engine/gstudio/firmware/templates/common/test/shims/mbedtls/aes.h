// Native-test shim: the mbedtls AES API subset the firmware uses (setkey_enc + crypt_ecb encrypt),
// as a FIPS-197 reference implementation. test_core checks it against NIST SP 800-38A vectors.
// The ESP32-S3 build uses ESP-IDF's real mbedtls instead.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define MBEDTLS_AES_ENCRYPT 1
#define MBEDTLS_AES_DECRYPT 0

typedef struct {
  int nr;
  uint32_t rk[60];
} mbedtls_aes_context;

namespace gs_aes_detail {
inline const uint8_t* sbox() {
  static uint8_t s[256];
  static bool done = false;
  if (!done) {
    // Generate the S-box from GF(2^8) inverses and the affine map (FIPS-197 5.1.1).
    uint8_t p = 1, q = 1;
    do {
      p = uint8_t(p ^ (p << 1) ^ (p & 0x80 ? 0x1B : 0));
      q ^= uint8_t(q << 1);
      q ^= uint8_t(q << 2);
      q ^= uint8_t(q << 4);
      if (q & 0x80) q ^= 0x09;
      const uint8_t x = uint8_t(q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4));
      s[p] = uint8_t(x ^ 0x63);
    } while (p != 1);
    s[0] = 0x63;
    done = true;
  }
  return s;
}
inline uint8_t xt(uint8_t x) { return uint8_t((x << 1) ^ (x & 0x80 ? 0x1B : 0)); }
}  // namespace gs_aes_detail

inline void mbedtls_aes_init(mbedtls_aes_context* c) { memset(c, 0, sizeof(*c)); }
inline void mbedtls_aes_free(mbedtls_aes_context* c) { memset(c, 0, sizeof(*c)); }

inline int mbedtls_aes_setkey_enc(mbedtls_aes_context* c, const unsigned char* key, unsigned bits) {
  const int nk = int(bits / 32);
  if (bits != 128 && bits != 192 && bits != 256) return -0x20;
  const uint8_t* s = gs_aes_detail::sbox();
  c->nr = nk + 6;
  const int total = 4 * (c->nr + 1);
  for (int i = 0; i < nk; ++i)
    c->rk[i] = uint32_t(key[4 * i]) << 24 | uint32_t(key[4 * i + 1]) << 16 | uint32_t(key[4 * i + 2]) << 8 | key[4 * i + 3];
  uint8_t rcon = 1;
  for (int i = nk; i < total; ++i) {
    uint32_t t = c->rk[i - 1];
    if (i % nk == 0) {
      t = (t << 8) | (t >> 24);
      t = uint32_t(s[t >> 24]) << 24 | uint32_t(s[(t >> 16) & 255]) << 16 | uint32_t(s[(t >> 8) & 255]) << 8 | s[t & 255];
      t ^= uint32_t(rcon) << 24;
      rcon = gs_aes_detail::xt(rcon);
    } else if (nk > 6 && i % nk == 4) {
      t = uint32_t(s[t >> 24]) << 24 | uint32_t(s[(t >> 16) & 255]) << 16 | uint32_t(s[(t >> 8) & 255]) << 8 | s[t & 255];
    }
    c->rk[i] = c->rk[i - nk] ^ t;
  }
  return 0;
}

inline int mbedtls_aes_crypt_ecb(mbedtls_aes_context* c, int mode, const unsigned char in[16], unsigned char out[16]) {
  if (mode != MBEDTLS_AES_ENCRYPT) return -0x21;  // the firmware only encrypts (CTR mode)
  const uint8_t* s = gs_aes_detail::sbox();
  uint8_t st[16];
  for (int i = 0; i < 16; ++i) st[i] = uint8_t(in[i] ^ (c->rk[i / 4] >> (24 - 8 * (i % 4))));
  for (int r = 1; r <= c->nr; ++r) {
    uint8_t t[16];
    for (int i = 0; i < 16; ++i) t[i] = s[st[i]];  // SubBytes
    for (int col = 0; col < 4; ++col)               // ShiftRows (state is column-major)
      for (int row = 0; row < 4; ++row) st[4 * col + row] = t[4 * ((col + row) % 4) + row];
    if (r != c->nr) {  // MixColumns
      for (int col = 0; col < 4; ++col) {
        uint8_t* a = st + 4 * col;
        const uint8_t a0 = a[0], a1 = a[1], a2 = a[2], a3 = a[3], all = uint8_t(a0 ^ a1 ^ a2 ^ a3);
        a[0] ^= uint8_t(all ^ gs_aes_detail::xt(uint8_t(a0 ^ a1)));
        a[1] ^= uint8_t(all ^ gs_aes_detail::xt(uint8_t(a1 ^ a2)));
        a[2] ^= uint8_t(all ^ gs_aes_detail::xt(uint8_t(a2 ^ a3)));
        a[3] ^= uint8_t(all ^ gs_aes_detail::xt(uint8_t(a3 ^ a0)));
      }
    }
    for (int i = 0; i < 16; ++i) st[i] ^= uint8_t(c->rk[4 * r + i / 4] >> (24 - 8 * (i % 4)));
  }
  memcpy(out, st, 16);
  return 0;
}
