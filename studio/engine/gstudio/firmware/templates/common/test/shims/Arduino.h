// Native-test shim of the Arduino-ESP32 core subset the firmware uses. Header-only.
#pragma once
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

#include "gs_host.h"

#define HIGH 1
#define LOW 0
#define INPUT 0x01
#define OUTPUT 0x03
#define INPUT_PULLUP 0x05
#define INPUT_PULLDOWN 0x09

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_TIMEOUT 0x107

// ------------------------------------------------------------------ String
class String {
 public:
  String() {}
  String(const char* s) : s_(s ? s : "") {}
  String(const std::string& s) : s_(s) {}
  String(char c) : s_(1, c) {}
  String(int v) : s_(std::to_string(v)) {}
  String(unsigned v) : s_(std::to_string(v)) {}
  String(long v) : s_(std::to_string(v)) {}
  String(unsigned long v) : s_(std::to_string(v)) {}
  String(long long v) : s_(std::to_string(v)) {}
  String(unsigned long long v) : s_(std::to_string(v)) {}
  String(double v, int decimals = 2) {
    char b[48];
    snprintf(b, sizeof(b), "%.*f", decimals, v);
    s_ = b;
  }
  const char* c_str() const { return s_.c_str(); }
  unsigned length() const { return unsigned(s_.size()); }
  bool isEmpty() const { return s_.empty(); }
  bool concat(const char* s) {
    s_ += s ? s : "";
    return true;
  }
  bool concat(const String& s) {
    s_ += s.s_;
    return true;
  }
  bool concat(char c) {
    s_ += c;
    return true;
  }
  void reserve(unsigned n) { s_.reserve(n); }
  String& operator+=(const String& o) {
    s_ += o.s_;
    return *this;
  }
  String& operator+=(const char* o) {
    s_ += o ? o : "";
    return *this;
  }
  String& operator+=(char c) {
    s_ += c;
    return *this;
  }
  String& operator+=(int v) {
    s_ += std::to_string(v);
    return *this;
  }
  friend String operator+(const String& a, const String& b) { return String(a.s_ + b.s_); }
  friend String operator+(const String& a, const char* b) { return String(a.s_ + (b ? b : "")); }
  friend String operator+(const char* a, const String& b) { return String(std::string(a ? a : "") + b.s_); }
  friend String operator+(const String& a, char c) { return String(a.s_ + c); }
  friend String operator+(const String& a, int v) { return String(a.s_ + std::to_string(v)); }
  friend String operator+(const String& a, unsigned v) { return String(a.s_ + std::to_string(v)); }
  friend String operator+(const String& a, long v) { return String(a.s_ + std::to_string(v)); }
  friend String operator+(const String& a, unsigned long v) { return String(a.s_ + std::to_string(v)); }
  bool operator==(const String& o) const { return s_ == o.s_; }
  bool operator==(const char* o) const { return s_ == (o ? o : ""); }
  bool operator!=(const String& o) const { return s_ != o.s_; }
  bool operator!=(const char* o) const { return !(*this == o); }
  bool operator<(const String& o) const { return s_ < o.s_; }
  char operator[](unsigned i) const { return i < s_.size() ? s_[i] : '\0'; }
  char charAt(unsigned i) const { return (*this)[i]; }
  bool startsWith(const String& p) const { return s_.compare(0, p.s_.size(), p.s_) == 0; }
  bool endsWith(const String& p) const {
    return s_.size() >= p.s_.size() && s_.compare(s_.size() - p.s_.size(), p.s_.size(), p.s_) == 0;
  }
  String substring(unsigned from) const { return from < s_.size() ? String(s_.substr(from)) : String(); }
  String substring(unsigned from, unsigned to) const {
    if (from >= s_.size() || to <= from) return String();
    return String(s_.substr(from, to - from));
  }
  int indexOf(char c) const {
    const size_t p = s_.find(c);
    return p == std::string::npos ? -1 : int(p);
  }
  int indexOf(const char* t) const {
    const size_t p = s_.find(t);
    return p == std::string::npos ? -1 : int(p);
  }
  long toInt() const { return strtol(s_.c_str(), nullptr, 10); }
  void toLowerCase() {
    for (char& c : s_)
      if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
  }
  void trim() {
    const size_t a = s_.find_first_not_of(" \t\r\n");
    const size_t b = s_.find_last_not_of(" \t\r\n");
    s_ = a == std::string::npos ? std::string() : s_.substr(a, b - a + 1);
  }
  const std::string& str() const { return s_; }

 private:
  std::string s_;
};

// ------------------------------------------------------------------ Print / Stream
class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t c) = 0;
  virtual size_t write(const uint8_t* b, size_t n) {
    size_t k = 0;
    while (k < n && write(b[k])) ++k;
    return k;
  }
  size_t print(const char* s) { return write(reinterpret_cast<const uint8_t*>(s), strlen(s)); }
  size_t print(const String& s) { return print(s.c_str()); }
  size_t print(int v) { return print(String(v)); }
  size_t println(const char* s = "") { return print(s) + print("\n"); }
  size_t println(const String& s) { return println(s.c_str()); }
  size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
    char b[1024];
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(b, sizeof(b), fmt, ap);
    va_end(ap);
    return print(n > 0 ? b : "");
  }
};

class Printable {
 public:
  virtual ~Printable() {}
  virtual size_t printTo(Print& p) const = 0;
};

class Stream : public Print {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() { return -1; }
  virtual size_t readBytes(char* buf, size_t n) {
    size_t k = 0;
    while (k < n) {
      const int c = read();
      if (c < 0) break;
      buf[k++] = char(c);
    }
    return k;
  }
  size_t readBytes(uint8_t* buf, size_t n) { return readBytes(reinterpret_cast<char*>(buf), n); }
  void setTimeout(unsigned long) {}
};

class HostSerial : public Stream {
 public:
  void begin(unsigned long) {}
  size_t write(uint8_t c) override {
    gs_host::serial_out += char(c);
    return 1;
  }
  using Print::write;
  int available() override { return int(gs_host::serial_in.size()); }
  int read() override {
    if (gs_host::serial_in.empty()) return -1;
    const int c = uint8_t(gs_host::serial_in[0]);
    gs_host::serial_in.erase(0, 1);
    return c;
  }
  operator bool() const { return true; }
};
inline HostSerial Serial;

// ------------------------------------------------------------------ IPAddress
class IPAddress {
 public:
  IPAddress() {}
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) : v_((uint32_t(a) << 24) | (uint32_t(b) << 16) | (uint32_t(c) << 8) | d) {}
  explicit IPAddress(uint32_t v) : v_(v) {}
  operator uint32_t() const { return v_; }
  String toString() const {
    char b[20];
    snprintf(b, sizeof(b), "%u.%u.%u.%u", unsigned(v_ >> 24), unsigned((v_ >> 16) & 255), unsigned((v_ >> 8) & 255),
             unsigned(v_ & 255));
    return String(b);
  }

 private:
  uint32_t v_ = 0;
};

// ------------------------------------------------------------------ time, GPIO, misc
inline uint32_t millis() { return gs_host::now_ms; }
inline uint32_t micros() { return gs_host::now_ms * 1000u; }
inline void delay(uint32_t ms) { gs_host::now_ms += ms; }
inline void yield() {}
inline void pinMode(int, int) {}
inline void digitalWrite(int pin, int v) { gs_host::gpio_out[pin] = v; }
inline int digitalRead(int pin) {
  if (gs_host::gpio_fn) return gs_host::gpio_fn(pin, gs_host::now_ms);
  auto it = gs_host::gpio_in.find(pin);
  return it == gs_host::gpio_in.end() ? HIGH : it->second;
}
inline int analogRead(int) { return 2048; }
inline void analogReadResolution(int) {}
inline void randomSeed(unsigned long s) { gs_host::rng_state = uint32_t(s) | 1u; }
inline long random(long hi) { return hi > 0 ? long(gs_host::next_random() % uint32_t(hi)) : 0; }
inline long random(long lo, long hi) { return hi > lo ? lo + random(hi - lo) : lo; }
inline bool ledcAttach(int, uint32_t, uint8_t) { return true; }
inline void ledcWrite(int, uint32_t) {}
inline void ledcWriteTone(int, uint32_t) {}
inline void ledcOutputInvert(int, bool) {}
inline void ledcDetach(int) {}

class EspClass {
 public:
  void restart() { ++gs_host::restarts; }
  uint32_t getFreeHeap() { return 200000; }
  uint32_t getPsramSize() { return 8u * 1024u * 1024u; }
  uint32_t getFlashChipSize() { return 8u * 1024u * 1024u; }
};
inline EspClass ESP;

#define SET_LOOP_TASK_STACK_SIZE(x)
#define PROGMEM

// ------------------------------------------------------------------ FreeRTOS subset
typedef int BaseType_t;
typedef unsigned UBaseType_t;
typedef uint32_t TickType_t;
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY 0xFFFFFFFFu
#define pdMS_TO_TICKS(ms) (TickType_t(ms))
struct GsHostQueue {
  size_t item = 0;
  size_t depth = 0;
  std::vector<std::vector<uint8_t>> items;
};
typedef GsHostQueue* QueueHandle_t;
typedef gs_host::Task* TaskHandle_t;
typedef void* SemaphoreHandle_t;
inline QueueHandle_t xQueueCreate(UBaseType_t depth, UBaseType_t item) {
  auto* q = new GsHostQueue;
  q->item = item;
  q->depth = depth;
  return q;
}
inline BaseType_t xQueueSend(QueueHandle_t q, const void* item, TickType_t) {
  if (!q || q->items.size() >= q->depth) return pdFALSE;
  const uint8_t* p = static_cast<const uint8_t*>(item);
  q->items.emplace_back(p, p + q->item);
  return pdTRUE;
}
inline BaseType_t xQueueReceive(QueueHandle_t q, void* out, TickType_t wait) {
  if (!q || q->items.empty()) {
    if (wait == portMAX_DELAY) throw gs_host::TaskExit();
    gs_host::now_ms += wait;
    return pdFALSE;
  }
  memcpy(out, q->items.front().data(), q->item);
  q->items.erase(q->items.begin());
  return pdTRUE;
}
inline BaseType_t xQueueReset(QueueHandle_t q) {
  if (q) q->items.clear();
  return pdPASS;
}
inline BaseType_t xTaskCreatePinnedToCore(void (*fn)(void*), const char* name, uint32_t, void* arg, UBaseType_t,
                                          TaskHandle_t* out, BaseType_t) {
  gs_host::Task& t = gs_host::tasks[name];
  t.fn = fn;
  t.arg = arg;
  if (out) *out = &t;
  return pdPASS;
}
inline void vTaskDelay(TickType_t t) { gs_host::now_ms += t; }
inline uint32_t ulTaskNotifyTake(BaseType_t clear, TickType_t) {
  gs_host::Task* t = gs_host::current;
  if (!t || t->notify == 0) throw gs_host::TaskExit();  // nothing to do: the pumped task would wait
  const uint32_t n = t->notify;
  t->notify = clear ? 0 : n - 1;
  return n;
}
inline BaseType_t xTaskNotifyGive(TaskHandle_t t) {
  if (t) ++t->notify;
  return pdPASS;
}
inline SemaphoreHandle_t xSemaphoreCreateMutex() { return reinterpret_cast<void*>(1); }
inline SemaphoreHandle_t xSemaphoreCreateRecursiveMutex() { return reinterpret_cast<void*>(1); }
inline BaseType_t xSemaphoreTake(SemaphoreHandle_t, TickType_t) { return pdTRUE; }
inline BaseType_t xSemaphoreGive(SemaphoreHandle_t) { return pdTRUE; }
inline BaseType_t xSemaphoreTakeRecursive(SemaphoreHandle_t, TickType_t) { return pdTRUE; }
inline BaseType_t xSemaphoreGiveRecursive(SemaphoreHandle_t) { return pdTRUE; }
inline TaskHandle_t xTaskGetCurrentTaskHandle() { return gs_host::current; }
