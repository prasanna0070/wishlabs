// Shared device services used by main.cpp, the self-test and the template apps.
#pragma once
#include "gs_kv_nvs.h"
#include "gs_store.h"

NvsKv& gs_kv();
gs::MemoryStore& gs_store();
// True while the factory self-test owns the hardware (apps pause).
bool gs_selftest_running();
