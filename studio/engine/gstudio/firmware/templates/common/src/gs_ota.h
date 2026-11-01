// Over-the-air update readiness.
// - Two app slots (ota_0, ota_1) in partitions.csv; the precompiled bootloader has app rollback on.
// - A new image boots in "pending verify". This firmware defers the Arduino core's automatic
//   "mark valid" (verifyRollbackLater) and marks it valid only after its own boot self-test has
//   passed and it has run for kConfirmAfterMs. A crash or reset before that rolls back.
// - Uploads over the local web API must carry an ECDSA P-256 signature (SHA-256 of the image) that
//   verifies against the public key compiled into this build. Builds without a key refuse uploads.
#pragma once
#include <Arduino.h>

namespace gsota {

constexpr uint32_t kConfirmAfterMs = 30000;

void begin();
// Call every loop; healthy = the boot self-test result.
void loop(uint32_t now, bool healthy);
bool pending_verify();
const char* state_name();
const char* running_partition();
bool signing_enabled();

// Streaming upload (called by the web server).
bool upload_begin(const char* signature_b64, String* error);
bool upload_write(const uint8_t* data, size_t len, String* error);
bool upload_end(String* error);  // verifies the signature, then sets the boot slot
void upload_abort();
uint32_t upload_bytes();

}  // namespace gsota
