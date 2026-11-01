// In-memory KvBackend with fault injection, for the store tests.
#pragma once
#include <map>
#include <string>
#include <vector>

#include "gs_store.h"

struct FakeKv : public gs::KvBackend {
  std::map<std::string, std::vector<uint8_t>> data;
  int fail_next_put = 0;       // > 0: the next put fails cleanly (nothing written)
  int torn_next_put = 0;       // > 0: the next put writes only half the bytes, then reports failure
  bool fail_reads = false;
  int puts = 0;

  int get(const char* key, uint8_t* buf, size_t cap) override {
    if (fail_reads) return -2;
    auto it = data.find(key);
    if (it == data.end()) return -1;
    if (it->second.size() > cap) return -2;
    for (size_t i = 0; i < it->second.size(); ++i) buf[i] = it->second[i];
    return static_cast<int>(it->second.size());
  }
  bool put(const char* key, const uint8_t* buf, size_t len) override {
    ++puts;
    if (fail_next_put > 0) {
      --fail_next_put;
      return false;
    }
    if (torn_next_put > 0) {
      --torn_next_put;
      data[key] = std::vector<uint8_t>(buf, buf + len / 2);  // power cut mid-write
      return false;
    }
    data[key] = std::vector<uint8_t>(buf, buf + len);
    return true;
  }
  bool erase(const char* key) override {
    data.erase(key);
    return true;
  }
};
