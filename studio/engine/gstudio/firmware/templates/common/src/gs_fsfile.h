// gs::ByteFile over an Arduino fs::File (microSD), for the clip reader / writer and zero-fill.
#pragma once
#include <FS.h>

#include "gs_clip.h"

class GsFsFile : public gs::ByteFile {
 public:
  explicit GsFsFile(fs::File& f) : f_(f) {}
  size_t read(uint8_t* b, size_t n) override { return f_ ? f_.read(b, n) : 0; }
  size_t write(const uint8_t* b, size_t n) override { return f_ ? f_.write(b, n) : 0; }
  bool seek(uint32_t p) override { return f_ && f_.seek(p); }
  uint32_t size() override { return f_ ? uint32_t(f_.size()) : 0; }
  void flush() override {
    if (f_) f_.flush();
  }

 private:
  fs::File& f_;
};
