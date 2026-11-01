#include <unity.h>

#include <string.h>

#include "fake_kv.h"
#include "gs_store.h"
#include "gs_util.h"

using gs::MemoryStore;
using gs::StoreResult;
using gs::StoreStatus;

static gs::MemoryLimits limits() {
  gs::MemoryLimits l;
  l.max_favourites = 12;
  l.volume_max = 60;
  return l;
}

static gs::Memory defaults() {
  gs::Memory m;
  m.volume = 35;
  return m;
}

static void test_crc32_known_vector() {
  const char* s = "123456789";
  TEST_ASSERT_EQUAL_HEX32(0xCBF43926u, gs::crc32(reinterpret_cast<const uint8_t*>(s), 9));
}

static void test_empty_backend_loads_defaults() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Empty, st.load());
  TEST_ASSERT_EQUAL(0, st.mem().count);
  TEST_ASSERT_EQUAL(35, st.mem().volume);
  TEST_ASSERT_TRUE(st.writable());
}

static void test_saved_name_survives_restart() {
  FakeKv kv;
  {
    MemoryStore st(kv, limits(), defaults());
    st.load();
    uint8_t id = 0;
    TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_name("  Anya   Rao ", &id));
    TEST_ASSERT_NOT_EQUAL(0, id);
  }
  MemoryStore again(kv, limits(), defaults());  // a fresh boot reading the same flash
  TEST_ASSERT_EQUAL(StoreStatus::Ok, again.load());
  TEST_ASSERT_EQUAL(1, again.mem().count);
  TEST_ASSERT_EQUAL_STRING("Anya Rao", again.mem().fav[0].name);
}

static void test_capacity_and_case_insensitive_duplicates() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  const char* names[12] = {"Anya", "Kabir", "Meera", "Arjun", "Ishaan", "Diya",
                           "Rohan", "Tara", "Veer", "Zoya", "Neel", "Sara"};
  for (int i = 0; i < 12; ++i) TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_name(names[i]));
  TEST_ASSERT_EQUAL(StoreResult::NoChange, st.add_name("anya"));
  const std::vector<uint8_t> before_a = kv.data["memA"];
  const std::vector<uint8_t> before_b = kv.data["memB"];
  TEST_ASSERT_EQUAL(StoreResult::Full, st.add_name("One More"));
  TEST_ASSERT_EQUAL(12, st.mem().count);
  TEST_ASSERT_TRUE(before_a == kv.data["memA"]);  // a refused add leaves the bytes untouched
  TEST_ASSERT_TRUE(before_b == kv.data["memB"]);
}

static void test_bad_names_rejected_and_nothing_written() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  const int puts = kv.puts;
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name("<script>"));
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name(""));
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name("   "));
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name("R2D2"));
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name("---"));
  TEST_ASSERT_EQUAL(StoreResult::InvalidName, st.add_name("Abcdefghijklmnopqrstuvwxyzabcdefg"));  // 33
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_name("D'Souza-Iyer Jr."));
  TEST_ASSERT_EQUAL(puts + 1, kv.puts);
}

static void test_power_cut_mid_write_keeps_previous_record() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_name("Anya"));
  kv.torn_next_put = 1;  // the power fails while the next record is being written
  TEST_ASSERT_EQUAL(StoreResult::StorageError, st.add_name("Kabir"));
  TEST_ASSERT_EQUAL(1, st.mem().count);  // RAM keeps the committed state
  MemoryStore boot(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Recovered, boot.load());
  TEST_ASSERT_EQUAL(1, boot.mem().count);
  TEST_ASSERT_EQUAL_STRING("Anya", boot.mem().fav[0].name);
  // The damaged slot is simply the next one written.
  TEST_ASSERT_EQUAL(StoreResult::Ok, boot.add_name("Kabir"));
  MemoryStore boot2(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Ok, boot2.load());
  TEST_ASSERT_EQUAL(2, boot2.mem().count);
}

static void test_failed_write_changes_nothing() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  st.add_name("Anya");
  const uint32_t seq = st.seq();
  kv.fail_next_put = 1;
  TEST_ASSERT_EQUAL(StoreResult::StorageError, st.set_volume(20));
  TEST_ASSERT_EQUAL(35, st.mem().volume);
  TEST_ASSERT_EQUAL_UINT32(seq, st.seq());
}

static void test_corrupt_memory_is_refused_not_overwritten() {
  FakeKv kv;
  kv.data["memA"] = std::vector<uint8_t>{'b', 'r', 'o', 'k', 'e', 'n'};
  kv.data["memB"] = std::vector<uint8_t>{'G', 'S', 'M', '1', 0, 0};
  MemoryStore st(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Corrupt, st.load());
  TEST_ASSERT_FALSE(st.writable());
  TEST_ASSERT_EQUAL(StoreResult::Refused, st.add_name("Anya"));
  TEST_ASSERT_EQUAL(StoreResult::Refused, st.set_volume(10));
  TEST_ASSERT_EQUAL(StoreResult::Refused, st.erase_all());
  TEST_ASSERT_EQUAL(6, kv.data["memA"].size());
  TEST_ASSERT_EQUAL('b', kv.data["memA"][0]);
  // Only the explicit parent reset clears it.
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.reset_memory());
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_name("Anya"));
  MemoryStore boot(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Ok, boot.load());
  TEST_ASSERT_EQUAL(1, boot.mem().count);
}

static void test_single_flipped_bit_is_detected() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  st.add_name("Anya");   // seq 1 -> memA
  st.add_name("Kabir");  // seq 2 -> memB
  std::vector<uint8_t>& newest = kv.data["memB"];
  newest[14] ^= 0x04;    // flip one bit inside the payload
  MemoryStore boot(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Recovered, boot.load());
  TEST_ASSERT_EQUAL(1, boot.mem().count);  // fell back to the last good record
  TEST_ASSERT_EQUAL_STRING("Anya", boot.mem().fav[0].name);
}

static void test_slots_alternate_and_newest_wins() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  for (int v = 10; v < 20; ++v) TEST_ASSERT_EQUAL(StoreResult::Ok, st.set_volume(uint8_t(v)));
  TEST_ASSERT_TRUE(kv.data.count("memA") && kv.data.count("memB"));
  MemoryStore boot(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::Ok, boot.load());
  TEST_ASSERT_EQUAL(19, boot.mem().volume);
  TEST_ASSERT_EQUAL_UINT32(10, boot.seq());
}

static void test_sequence_wraparound() {
  gs::Memory a = defaults();
  a.volume = 11;
  gs::Memory b = defaults();
  b.volume = 22;
  uint8_t buf[gs::kRecordMax];
  FakeKv kv;
  size_t n = MemoryStore::encode(a, 0xFFFFFFFFu, buf, sizeof(buf));
  kv.data["memA"] = std::vector<uint8_t>(buf, buf + n);
  n = MemoryStore::encode(b, 0u, buf, sizeof(buf));  // written after the counter wrapped
  kv.data["memB"] = std::vector<uint8_t>(buf, buf + n);
  MemoryStore st(kv, limits(), defaults());
  st.load();
  TEST_ASSERT_EQUAL(22, st.mem().volume);
}

static void test_volume_limits() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  TEST_ASSERT_EQUAL(StoreResult::OutOfRange, st.set_volume(61));
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.set_volume(60));
  TEST_ASSERT_EQUAL(StoreResult::NoChange, st.set_volume(60));
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.set_night(true, 30, 20));
  TEST_ASSERT_EQUAL(StoreResult::OutOfRange, st.set_night(true, 121, 20));
}

static void test_clip_favourites_and_erase() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  uint8_t a = 0, b = 0;
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_clip(&a));
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.add_clip(&b));
  TEST_ASSERT_NOT_EQUAL(a, b);
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.set_name(a, "Chhota Bheem"));
  TEST_ASSERT_EQUAL(gs::kFavHasClip | gs::kFavHasName, st.find(a)->flags);
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.remove(b));
  TEST_ASSERT_NULL(st.find(b));
  TEST_ASSERT_EQUAL(StoreResult::NotFound, st.remove(b));
  st.set_volume(20);
  TEST_ASSERT_EQUAL(StoreResult::Ok, st.erase_all());
  TEST_ASSERT_EQUAL(0, st.mem().count);
  TEST_ASSERT_EQUAL(20, st.mem().volume);  // erase-all clears favourites, keeps settings
  MemoryStore boot(kv, limits(), defaults());
  boot.load();
  TEST_ASSERT_EQUAL(0, boot.mem().count);
}

static void test_ids_are_not_reused_while_in_use() {
  FakeKv kv;
  MemoryStore st(kv, limits(), defaults());
  st.load();
  uint8_t ids[12];
  for (int i = 0; i < 12; ++i) st.add_clip(&ids[i]);
  for (int i = 0; i < 12; ++i)
    for (int j = i + 1; j < 12; ++j) TEST_ASSERT_NOT_EQUAL(ids[i], ids[j]);
  st.remove(ids[3]);
  uint8_t fresh = 0;
  st.add_clip(&fresh);
  for (int i = 0; i < 12; ++i)
    if (i != 3) TEST_ASSERT_NOT_EQUAL(ids[i], fresh);
}

static void test_unreadable_storage_refuses_writes() {
  FakeKv kv;
  kv.fail_reads = true;
  MemoryStore st(kv, limits(), defaults());
  TEST_ASSERT_EQUAL(StoreStatus::StorageError, st.load());
  TEST_ASSERT_EQUAL(StoreResult::Refused, st.add_name("Anya"));
}

void register_store_tests() {
  RUN_TEST(test_crc32_known_vector);
  RUN_TEST(test_empty_backend_loads_defaults);
  RUN_TEST(test_saved_name_survives_restart);
  RUN_TEST(test_capacity_and_case_insensitive_duplicates);
  RUN_TEST(test_bad_names_rejected_and_nothing_written);
  RUN_TEST(test_power_cut_mid_write_keeps_previous_record);
  RUN_TEST(test_failed_write_changes_nothing);
  RUN_TEST(test_corrupt_memory_is_refused_not_overwritten);
  RUN_TEST(test_single_flipped_bit_is_detected);
  RUN_TEST(test_slots_alternate_and_newest_wins);
  RUN_TEST(test_sequence_wraparound);
  RUN_TEST(test_volume_limits);
  RUN_TEST(test_clip_favourites_and_erase);
  RUN_TEST(test_ids_are_not_reused_while_in_use);
  RUN_TEST(test_unreadable_storage_refuses_writes);
}
