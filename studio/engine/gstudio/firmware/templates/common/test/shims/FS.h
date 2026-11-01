// Native-test shim of Arduino-ESP32 fs::File / fs::FS over gs_host::sd (an in-memory card).
#pragma once
#include <memory>

#include "Arduino.h"

#define FILE_READ "r"
#define FILE_WRITE "w"
#define FILE_APPEND "a"

namespace fs {

struct HostOpen {
  std::string path;
  bool dir = false;
  bool can_write = false;
  bool can_read = false;
  size_t pos = 0;
  std::vector<std::string> listing;  // for directories
  size_t next = 0;
};

inline std::string host_base(const std::string& p) {
  const size_t s = p.find_last_of('/');
  return s == std::string::npos ? p : p.substr(s + 1);
}

class File : public Stream {
 public:
  File() {}
  explicit File(std::shared_ptr<HostOpen> h) : h_(std::move(h)) {
    if (h_) name_ = host_base(h_->path);
  }
  explicit operator bool() const { return h_ && gs_host::sd.present && (h_->dir || gs_host::sd.files.count(h_->path)); }
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* b, size_t n) override {
    if (!*this || !h_->can_write) return 0;
    auto& v = gs_host::sd.files[h_->path];
    if (h_->pos + n > v.size()) v.resize(h_->pos + n);
    memcpy(v.data() + h_->pos, b, n);
    h_->pos += n;
    return n;
  }
  int available() override {
    if (!*this || h_->dir) return 0;
    const auto& v = gs_host::sd.files[h_->path];
    return h_->pos < v.size() ? int(v.size() - h_->pos) : 0;
  }
  int read() override {
    uint8_t c;
    return read(&c, 1) == 1 ? c : -1;
  }
  size_t read(uint8_t* b, size_t n) {
    if (!*this || h_->dir || !h_->can_read) return 0;
    const auto& v = gs_host::sd.files[h_->path];
    size_t k = 0;
    while (k < n && h_->pos < v.size()) b[k++] = v[h_->pos++];
    return k;
  }
  size_t readBytes(char* b, size_t n) override { return read(reinterpret_cast<uint8_t*>(b), n); }
  int peek() override {
    if (!*this || h_->dir) return -1;
    const auto& v = gs_host::sd.files[h_->path];
    return h_->pos < v.size() ? v[h_->pos] : -1;
  }
  bool seek(uint32_t pos) {
    if (!*this || h_->dir) return false;
    if (pos > gs_host::sd.files[h_->path].size() && !h_->can_write) return false;
    h_->pos = pos;
    return true;
  }
  size_t position() const { return h_ ? h_->pos : 0; }
  size_t size() const {
    if (!h_ || h_->dir) return 0;
    auto it = gs_host::sd.files.find(h_->path);
    return it == gs_host::sd.files.end() ? 0 : it->second.size();
  }
  void flush() {}
  void close() { h_.reset(); }
  const char* path() const { return h_ ? h_->path.c_str() : ""; }
  const char* name() const { return name_.c_str(); }
  bool isDirectory() const { return h_ && h_->dir; }
  File openNextFile(const char* mode = FILE_READ) {
    (void)mode;
    if (!h_ || !h_->dir) return File();
    while (h_->next < h_->listing.size()) {
      const std::string p = h_->listing[h_->next++];
      auto o = std::make_shared<HostOpen>();
      o->path = p;
      o->dir = gs_host::sd.dirs.count(p) > 0;
      o->can_read = true;
      if (o->dir || gs_host::sd.files.count(p)) return File(o);
    }
    return File();
  }

 private:
  std::shared_ptr<HostOpen> h_;
  std::string name_;
};

class FS {
 public:
  File open(const char* path, const char* mode = FILE_READ, bool create = false) {
    (void)create;
    if (!mounted_ || !gs_host::sd.present || !path) return File();
    const std::string p = path;
    auto o = std::make_shared<HostOpen>();
    o->path = p;
    if (gs_host::sd.dirs.count(p)) {
      o->dir = true;
      for (const auto& kv : gs_host::sd.files)
        if (kv.first.size() > p.size() + 1 && kv.first.compare(0, p.size() + 1, p + "/") == 0 &&
            kv.first.find('/', p.size() + 1) == std::string::npos)
          o->listing.push_back(kv.first);
      for (const auto& d : gs_host::sd.dirs)
        if (d.size() > p.size() + 1 && d.compare(0, p.size() + 1, p + "/") == 0 && d.find('/', p.size() + 1) == std::string::npos)
          o->listing.push_back(d);
      return File(o);
    }
    const std::string m = mode ? mode : "r";
    const bool exists = gs_host::sd.files.count(p) > 0;
    const std::string parent = p.substr(0, p.find_last_of('/'));
    if (m == "r") {
      if (!exists) return File();
      o->can_read = true;
      gs_host::sd.reads.push_back(p);
    } else if (m == "r+") {
      if (!exists) return File();
      o->can_read = o->can_write = true;
    } else {
      if (!parent.empty() && !gs_host::sd.dirs.count(parent)) return File();
      if (m == "w" || m == "w+") gs_host::sd.files[p].clear();
      else gs_host::sd.files[p];
      o->can_write = true;
      o->can_read = m.find('+') != std::string::npos;
      if (m[0] == 'a') o->pos = gs_host::sd.files[p].size();
    }
    return File(o);
  }
  File open(const String& path, const char* mode = FILE_READ) { return open(path.c_str(), mode); }
  bool exists(const char* path) {
    return mounted_ && gs_host::sd.present && (gs_host::sd.files.count(path) || gs_host::sd.dirs.count(path));
  }
  bool exists(const String& p) { return exists(p.c_str()); }
  bool remove(const char* path) {
    if (!mounted_ || !gs_host::sd.present) return false;
    ++gs_host::sd.removes;
    if (gs_host::sd.fail_all_removes || gs_host::sd.fail_remove.count(path)) return false;
    auto it = gs_host::sd.files.find(path);
    if (it == gs_host::sd.files.end()) return false;
    gs_host::sd.removed[path] = it->second;
    gs_host::sd.files.erase(it);
    return true;
  }
  bool remove(const String& p) { return remove(p.c_str()); }
  bool rename(const char* from, const char* to) {
    if (!mounted_ || !gs_host::sd.present || gs_host::sd.fail_rename) return false;
    auto it = gs_host::sd.files.find(from);
    if (it == gs_host::sd.files.end() || gs_host::sd.files.count(to)) return false;
    gs_host::sd.files[to] = it->second;
    gs_host::sd.files.erase(from);
    return true;
  }
  bool mkdir(const char* path) {
    if (!mounted_ || !gs_host::sd.present) return false;
    gs_host::sd.dirs.insert(path);
    return true;
  }
  bool rmdir(const char* path) { return gs_host::sd.dirs.erase(path) > 0; }

 protected:
  bool mounted_ = false;
};

}  // namespace fs

using fs::File;
