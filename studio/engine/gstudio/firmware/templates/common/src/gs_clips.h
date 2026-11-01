// Voice-clip protection on the device (child_voice_clip mode; MAJ-1):
//   - the AES-256 clip key: 32 random bytes in the gsnvs partition, created at the first recording,
//     destroyed by "erase all" and "reset memory" (crypto-erase: old copies on the card stay unreadable);
//   - the powered-on hour counter that clip retention is measured in (the gadget has no clock
//     unless it is on Wi-Fi, so retention counts hours it was switched on).
#pragma once
#include <stdint.h>

#include "gs_clip.h"
#include "gs_store.h"

namespace gsclips {

void begin(gs::KvBackend* kv);
// Copies the key into out. With create, a missing key is generated and saved first.
bool key(uint8_t out[gs::kClipKeyBytes], bool create);
bool has_key();
bool destroy_key();
void new_nonce(uint8_t out[gs::kClipNonceBytes]);
uint32_t hours();
// Call every loop: saves the hour counter once per powered-on hour.
void loop(uint32_t now);

}  // namespace gsclips
