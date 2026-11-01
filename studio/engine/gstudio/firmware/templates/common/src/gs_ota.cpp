#include "gs_ota.h"

#include <esp_ota_ops.h>
#include <mbedtls/base64.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>

#include "config.h"

// The Arduino core marks a freshly updated app valid at boot unless this returns true.
// We take over that decision so a new image must pass our own checks first.
extern "C" bool verifyRollbackLater() { return true; }

namespace gsota {

static bool s_pending = false;
static bool s_decided = false;
static const char* s_state = "unknown";
static esp_ota_handle_t s_handle = 0;
static const esp_partition_t* s_target = nullptr;
static mbedtls_sha256_context s_sha;
static uint8_t s_sig[160];
static size_t s_sig_len = 0;
static uint32_t s_bytes = 0;
static bool s_active = false;

void begin() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  if (running && esp_ota_get_state_partition(running, &st) == ESP_OK) {
    s_pending = st == ESP_OTA_IMG_PENDING_VERIFY;
    switch (st) {
      case ESP_OTA_IMG_NEW: s_state = "new"; break;
      case ESP_OTA_IMG_PENDING_VERIFY: s_state = "pending_verify"; break;
      case ESP_OTA_IMG_VALID: s_state = "valid"; break;
      case ESP_OTA_IMG_INVALID: s_state = "invalid"; break;
      case ESP_OTA_IMG_ABORTED: s_state = "aborted"; break;
      default: s_state = "undefined"; break;
    }
  } else {
    s_state = "factory_or_serial_flash";  // flashed over USB: no OTA state recorded
  }
}

void loop(uint32_t now, bool healthy) {
  if (!s_pending || s_decided) return;
  if (!healthy) {
    s_decided = true;
    Serial.println("GS-OTA {\"event\":\"rollback\",\"reason\":\"boot self-test failed\"}");
    delay(50);
    esp_ota_mark_app_invalid_rollback_and_reboot();
    return;
  }
  if (now >= kConfirmAfterMs) {
    s_decided = true;
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
      s_pending = false;
      s_state = "valid";
      Serial.println("GS-OTA {\"event\":\"marked_valid\"}");
    }
  }
}

bool pending_verify() { return s_pending; }
const char* state_name() { return s_state; }

const char* running_partition() {
  const esp_partition_t* p = esp_ota_get_running_partition();
  return p ? p->label : "?";
}

bool signing_enabled() { return sizeof(GS_OTA_PUBKEY_PEM) > 1; }

bool upload_begin(const char* signature_b64, String* error) {
  upload_abort();
  if (!signing_enabled()) {
    *error = "Updates over Wi-Fi are off in this build (no signing key). Flash over USB instead.";
    return false;
  }
  if (s_pending) {
    *error = "The current firmware is still being verified. Try again in a minute.";
    return false;
  }
  if (!signature_b64 || !signature_b64[0]) {
    *error = "Missing X-GS-Signature header.";
    return false;
  }
  if (mbedtls_base64_decode(s_sig, sizeof(s_sig), &s_sig_len, reinterpret_cast<const unsigned char*>(signature_b64),
                            strlen(signature_b64)) != 0 ||
      s_sig_len < 8) {
    *error = "The signature is not valid base64.";
    return false;
  }
  s_target = esp_ota_get_next_update_partition(nullptr);
  if (!s_target) {
    *error = "No update slot in the partition table.";
    return false;
  }
  const esp_err_t e = esp_ota_begin(s_target, OTA_WITH_SEQUENTIAL_WRITES, &s_handle);
  if (e != ESP_OK) {
    *error = String("Could not start the update: ") + esp_err_to_name(e);
    s_handle = 0;
    return false;
  }
  mbedtls_sha256_init(&s_sha);
  mbedtls_sha256_starts(&s_sha, 0);
  s_bytes = 0;
  s_active = true;
  return true;
}

bool upload_write(const uint8_t* data, size_t len, String* error) {
  if (!s_active) {
    *error = "No update in progress.";
    return false;
  }
  const esp_err_t e = esp_ota_write(s_handle, data, len);
  if (e != ESP_OK) {
    *error = String("Write failed: ") + esp_err_to_name(e);
    upload_abort();
    return false;
  }
  mbedtls_sha256_update(&s_sha, data, len);
  s_bytes += uint32_t(len);
  return true;
}

static bool verify(const uint8_t hash[32]) {
  mbedtls_pk_context pk;
  mbedtls_pk_init(&pk);
  const char* pem = GS_OTA_PUBKEY_PEM;
  bool ok = mbedtls_pk_parse_public_key(&pk, reinterpret_cast<const unsigned char*>(pem), strlen(pem) + 1) == 0;
  if (ok) ok = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, 32, s_sig, s_sig_len) == 0;
  mbedtls_pk_free(&pk);
  return ok;
}

bool upload_end(String* error) {
  if (!s_active) {
    *error = "No update in progress.";
    return false;
  }
  uint8_t hash[32];
  mbedtls_sha256_finish(&s_sha, hash);
  mbedtls_sha256_free(&s_sha);
  s_active = false;
  if (!verify(hash)) {
    esp_ota_abort(s_handle);
    s_handle = 0;
    *error = "Signature check failed: this image was not signed for this device. Nothing changed.";
    return false;
  }
  esp_err_t e = esp_ota_end(s_handle);  // also validates the image format and checksum
  s_handle = 0;
  if (e != ESP_OK) {
    *error = String("The image is not a valid app: ") + esp_err_to_name(e);
    return false;
  }
  e = esp_ota_set_boot_partition(s_target);
  if (e != ESP_OK) {
    *error = String("Could not select the new slot: ") + esp_err_to_name(e);
    return false;
  }
  return true;
}

void upload_abort() {
  if (s_active) {
    mbedtls_sha256_free(&s_sha);
    s_active = false;
  }
  if (s_handle) {
    esp_ota_abort(s_handle);
    s_handle = 0;
  }
}

uint32_t upload_bytes() { return s_bytes; }

}  // namespace gsota
