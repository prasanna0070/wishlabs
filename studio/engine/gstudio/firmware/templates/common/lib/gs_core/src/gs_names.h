// Favourite-name validation, shared by the parent page API and the store.
#pragma once
#include <stddef.h>

namespace gs {

constexpr size_t kNameMax = 32;  // characters, not counting the terminator

// Collapses runs of spaces, trims, and checks the result: 1..kNameMax characters of
// A-Z, a-z, space, apostrophe, hyphen or full stop, with at least one letter.
// Writes the cleaned name to out (cap >= kNameMax + 1) and returns true when valid.
// Names in other scripts (Devanagari, Tamil) are not accepted yet: the device font and the
// story-pack file names are ASCII. The parent page says so.
bool clean_name(const char* in, char* out, size_t cap);

// Case-insensitive ASCII equality.
bool name_equal_ci(const char* a, const char* b);

// File-name slug: lower case, spaces and hyphens to '_', other punctuation dropped.
// "Anya Rao" -> "anya_rao". Used for story-pack name clips (/names/<slug>.wav).
void name_slug(const char* name, char* out, size_t cap);

}  // namespace gs
