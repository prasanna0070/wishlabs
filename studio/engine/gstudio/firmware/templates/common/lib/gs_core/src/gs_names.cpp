#include "gs_names.h"

namespace gs {

static bool is_alpha(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }
static char lower(char c) { return (c >= 'A' && c <= 'Z') ? char(c - 'A' + 'a') : c; }

bool clean_name(const char* in, char* out, size_t cap) {
  if (!out || cap == 0) return false;
  out[0] = '\0';
  if (!in || cap < 2) return false;
  size_t n = 0;
  bool pending_space = false;
  bool any_letter = false;
  for (const char* p = in; *p; ++p) {
    char c = *p;
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
      pending_space = n > 0;
      continue;
    }
    if (!(is_alpha(c) || c == '\'' || c == '-' || c == '.')) {
      out[0] = '\0';
      return false;
    }
    if (pending_space) {
      if (n + 1 >= cap || n + 1 > kNameMax) {
        out[0] = '\0';
        return false;
      }
      out[n++] = ' ';
      pending_space = false;
    }
    if (n + 1 >= cap || n + 1 > kNameMax) {
      out[0] = '\0';
      return false;
    }
    out[n++] = c;
    any_letter = any_letter || is_alpha(c);
  }
  out[n] = '\0';
  if (n == 0 || !any_letter) {
    out[0] = '\0';
    return false;
  }
  return true;
}

bool name_equal_ci(const char* a, const char* b) {
  if (!a || !b) return false;
  while (*a && *b) {
    if (lower(*a) != lower(*b)) return false;
    ++a;
    ++b;
  }
  return *a == *b;
}

void name_slug(const char* name, char* out, size_t cap) {
  if (!out || cap == 0) return;
  size_t n = 0;
  for (const char* p = name; p && *p && n + 1 < cap; ++p) {
    char c = *p;
    if (is_alpha(c)) {
      out[n++] = lower(c);
    } else if ((c == ' ' || c == '-') && n > 0 && out[n - 1] != '_') {
      out[n++] = '_';
    }
  }
  while (n > 0 && out[n - 1] == '_') --n;
  out[n] = '\0';
}

}  // namespace gs
