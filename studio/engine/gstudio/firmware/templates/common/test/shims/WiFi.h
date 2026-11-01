// Native-test shim of the Arduino-ESP32 WiFi subset used by src/gs_net.cpp, over gs_host::wifi.
#pragma once
#include "Arduino.h"

typedef enum { WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3 } wifi_mode_t;
typedef enum { WIFI_AUTH_OPEN = 0, WIFI_AUTH_WPA2_PSK = 3 } wifi_auth_mode_t;
typedef enum { WL_IDLE_STATUS = 0, WL_CONNECTED = 3, WL_DISCONNECTED = 6 } wl_status_t;

class WiFiClient {
 public:
  IPAddress remoteIP() const { return IPAddress(gs_host::wifi.client_ip); }
};

class WiFiClass {
 public:
  bool mode(int m) {
    gs_host::wifi.mode = m;
    return true;
  }
  int getMode() const { return gs_host::wifi.mode; }
  bool softAP(const char* ssid, const char* pass = nullptr, int = 1, int = 0, int = 4) {
    ++gs_host::wifi.softap_calls;
    gs_host::wifi.ap_on = true;
    gs_host::wifi.ap_ssid = ssid ? ssid : "";
    gs_host::wifi.ap_pass = pass ? pass : "";
    return true;
  }
  bool softAPdisconnect(bool = false) {
    gs_host::wifi.ap_on = false;
    return true;
  }
  IPAddress softAPIP() { return IPAddress(192, 168, 4, 1); }
  uint8_t softAPgetStationNum() { return uint8_t(gs_host::wifi.station_num); }
  uint8_t* macAddress(uint8_t* mac) {
    const uint8_t m[6] = {0x24, 0x6F, 0x28, 0x12, 0xAB, 0xCD};
    memcpy(mac, m, 6);
    return mac;
  }
  String macAddress() { return String("24:6F:28:12:AB:CD"); }
  wl_status_t status() { return gs_host::wifi.connected ? WL_CONNECTED : WL_DISCONNECTED; }
  IPAddress localIP() { return IPAddress(10, 0, 0, 42); }
  int8_t RSSI() { return -55; }
  int8_t RSSI(int) { return -60; }
  String SSID(int i) { return String("HomeNet") + String(i); }
  wifi_auth_mode_t encryptionType(int) { return WIFI_AUTH_WPA2_PSK; }
  int16_t scanNetworks(bool = false, bool = false) { return 2; }
  void scanDelete() {}
  bool setHostname(const char*) { return true; }
  bool setAutoReconnect(bool) { return true; }
  void persistent(bool) {}
  int begin(const char* ssid, const char* = nullptr) {
    gs_host::wifi.sta_ssid = ssid ? ssid : "";
    return WL_DISCONNECTED;
  }
  bool disconnect(bool = false, bool = false) {
    gs_host::wifi.connected = false;
    return true;
  }
};
inline WiFiClass WiFi;
