// Native-test shim of the Arduino-ESP32 WebServer subset used by src/gs_net.cpp. Tests dispatch
// requests with request() / upload() and read the reply from last().
#pragma once
#include <functional>

#include "FS.h"
#include "WiFi.h"

typedef enum { HTTP_ANY = 0, HTTP_GET = 1, HTTP_POST = 3 } HTTPMethod;
typedef enum { UPLOAD_FILE_START, UPLOAD_FILE_WRITE, UPLOAD_FILE_END, UPLOAD_FILE_ABORTED } HTTPUploadStatus;
#define HTTP_UPLOAD_BUFLEN 1436
#define CONTENT_LENGTH_UNKNOWN ((size_t)-1)

struct HTTPUpload {
  HTTPUploadStatus status = UPLOAD_FILE_START;
  String filename, name, type;
  size_t totalSize = 0;
  size_t currentSize = 0;
  uint8_t buf[HTTP_UPLOAD_BUFLEN];
};

struct HostReply {
  int status = 0;
  std::string type;
  std::string body;
  std::map<std::string, std::string> headers;
};

class WebServer {
 public:
  typedef std::function<void(void)> THandler;
  explicit WebServer(int port = 80) : port_(port) { instance() = this; }
  static WebServer*& instance() {
    static WebServer* s = nullptr;
    return s;
  }
  void on(const char* uri, HTTPMethod m, THandler h) { routes_.push_back({uri, m, h, nullptr}); }
  void on(const char* uri, HTTPMethod m, THandler h, THandler up) { routes_.push_back({uri, m, h, up}); }
  void onNotFound(THandler h) { not_found_ = h; }
  void collectHeaders(const char**, size_t) {}
  void begin() { begun_ = true; }
  void stop() { begun_ = false; }
  void handleClient() {}
  bool begun() const { return begun_; }

  String header(const char* name) {
    auto it = req_headers_.find(name);
    return it == req_headers_.end() ? String() : String(it->second);
  }
  bool hasHeader(const char* name) { return req_headers_.count(name) > 0; }
  String arg(const char* name) {
    auto it = args_.find(name);
    return it == args_.end() ? String() : String(it->second);
  }
  bool hasArg(const char* name) { return args_.count(name) > 0; }
  String hostHeader() { return String(host_); }
  String uri() { return String(uri_); }
  HTTPMethod method() { return method_; }
  WiFiClient& client() { return client_; }
  HTTPUpload& upload() { return upload_; }

  void sendHeader(const char* k, const String& v, bool = false) { reply_.headers[k] = v.c_str(); }
  void send(int code, const char* type = "", const String& body = String()) {
    reply_.status = code;
    reply_.type = type ? type : "";
    reply_.body += body.c_str();
  }
  void send_P(int code, const char* type, const char* body) { send(code, type, String(body)); }
  void setContentLength(size_t) {}
  void sendContent(const char* data, size_t n) { reply_.body.append(data, n); }
  void sendContent(const String& s) { reply_.body += s.c_str(); }
  size_t streamFile(fs::File& f, const String& type) {
    reply_.status = 200;
    reply_.type = type.c_str();
    uint8_t b[256];
    size_t total = 0, n;
    while ((n = f.read(b, sizeof(b))) > 0) {
      reply_.body.append(reinterpret_cast<char*>(b), n);
      total += n;
    }
    return total;
  }

  // ---- test side
  const HostReply& request(HTTPMethod m, const std::string& uri_with_query, const std::string& body = "",
                           const std::map<std::string, std::string>& headers = {}, const std::string& host = "10.0.0.42") {
    start(m, uri_with_query, headers, host);
    args_["plain"] = body;
    Route* r = find(m);
    if (r) r->handler();
    else if (not_found_) not_found_();
    return reply_;
  }
  const HostReply& upload_request(const std::string& uri_with_query, const std::string& data,
                                  const std::map<std::string, std::string>& headers = {}, size_t chunk = 1024) {
    start(HTTP_POST, uri_with_query, headers, "10.0.0.42");
    Route* r = find(HTTP_POST);
    if (!r) {
      if (not_found_) not_found_();
      return reply_;
    }
    upload_ = HTTPUpload();
    upload_.filename = "clip.wav";
    upload_.name = "clip";
    upload_.status = UPLOAD_FILE_START;
    if (r->upload) r->upload();
    size_t off = 0;
    while (off < data.size()) {
      const size_t k = std::min(chunk, std::min(data.size() - off, size_t(HTTP_UPLOAD_BUFLEN)));
      upload_.status = UPLOAD_FILE_WRITE;
      memcpy(upload_.buf, data.data() + off, k);
      upload_.currentSize = k;
      upload_.totalSize += k;
      off += k;
      if (r->upload) r->upload();
    }
    upload_.status = UPLOAD_FILE_END;
    upload_.currentSize = 0;
    if (r->upload) r->upload();
    r->handler();
    return reply_;
  }
  const HostReply& last() const { return reply_; }
  void reset_routes() {
    routes_.clear();
    not_found_ = nullptr;
    begun_ = false;
  }

 private:
  struct Route {
    std::string uri;
    HTTPMethod method;
    THandler handler;
    THandler upload;
  };
  void start(HTTPMethod m, const std::string& uq, const std::map<std::string, std::string>& headers,
             const std::string& host) {
    reply_ = HostReply();
    method_ = m;
    req_headers_ = headers;
    host_ = host;
    args_.clear();
    const size_t q = uq.find('?');
    uri_ = uq.substr(0, q);
    if (q != std::string::npos) {
      std::string rest = uq.substr(q + 1);
      while (!rest.empty()) {
        const size_t amp = rest.find('&');
        const std::string kv = rest.substr(0, amp);
        const size_t eq = kv.find('=');
        args_[kv.substr(0, eq)] = eq == std::string::npos ? "" : kv.substr(eq + 1);
        if (amp == std::string::npos) break;
        rest = rest.substr(amp + 1);
      }
    }
  }
  Route* find(HTTPMethod m) {
    for (Route& r : routes_)
      if (r.uri == uri_ && (r.method == HTTP_ANY || r.method == m)) return &r;
    return nullptr;
  }
  int port_;
  std::vector<Route> routes_;
  THandler not_found_;
  bool begun_ = false;
  std::map<std::string, std::string> req_headers_;
  std::map<std::string, std::string> args_;
  std::string host_, uri_;
  HTTPMethod method_ = HTTP_GET;
  WiFiClient client_;
  HTTPUpload upload_;
  HostReply reply_;
};
