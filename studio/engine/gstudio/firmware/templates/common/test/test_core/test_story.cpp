#include <unity.h>

#include <set>
#include <string>

#include "fake_kv.h"
#include "gs_story.h"

using gs::CaptureMode;
using gs::PlayItem;
using gs::PlayKind;

struct FakeCard {
  std::set<std::string> files;
};

static bool card_exists(const char* path, void* ctx) {
  return static_cast<FakeCard*>(ctx)->files.count(path) > 0;
}

static void test_shuffle_never_repeats_back_to_back() {
  gs::Rng rng(12345);
  int last = -1;
  std::set<int> seen;
  for (int i = 0; i < 500; ++i) {
    const int next = gs::pick_story(6, last, rng, false);
    TEST_ASSERT_TRUE(next >= 0 && next < 6);
    TEST_ASSERT_NOT_EQUAL(last, next);
    seen.insert(next);
    last = next;
  }
  TEST_ASSERT_EQUAL(6, seen.size());  // every story comes up
}

static void test_sequential_order_wraps() {
  gs::Rng rng(1);
  TEST_ASSERT_EQUAL(0, gs::pick_story(3, -1, rng, true));
  TEST_ASSERT_EQUAL(1, gs::pick_story(3, 0, rng, true));
  TEST_ASSERT_EQUAL(2, gs::pick_story(3, 1, rng, true));
  TEST_ASSERT_EQUAL(0, gs::pick_story(3, 2, rng, true));
  TEST_ASSERT_EQUAL(0, gs::pick_story(1, 0, rng, false));
  TEST_ASSERT_EQUAL(-1, gs::pick_story(0, -1, rng, false));
}

static void test_catalog_rejects_bad_entries() {
  gs::StoryCatalog cat;
  TEST_ASSERT_TRUE(cat.add("mango_tree", "The Mango Tree", 4));
  TEST_ASSERT_FALSE(cat.add("mango_tree", "dup", 4));
  TEST_ASSERT_FALSE(cat.add("../etc", "path trick", 2));
  TEST_ASSERT_FALSE(cat.add("Upper", "upper case", 2));
  TEST_ASSERT_FALSE(cat.add("zero", "no segments", 0));
  TEST_ASSERT_EQUAL(1, cat.count());
  TEST_ASSERT_EQUAL(0, cat.index_of("mango_tree"));
}

static gs::Memory memory_with_clip(uint8_t id) {
  gs::Memory m;
  m.count = 1;
  m.fav[0].id = id;
  m.fav[0].flags = gs::kFavHasClip;
  return m;
}

static void test_playlist_inserts_child_clip_between_segments() {
  gs::StoryCatalog cat;
  cat.add("monsoon_cloud", "The Sleepy Monsoon Cloud", 3);
  FakeCard card;
  card.files.insert("/fav/f007.wav");
  gs::Memory m = memory_with_clip(7);
  PlayItem items[gs::kMaxPlayItems];
  const size_t n = gs::build_playlist(*cat.get(0), m, 7, CaptureMode::ChildVoiceClip, card_exists, &card, items,
                                      gs::kMaxPlayItems);
  TEST_ASSERT_EQUAL(5, n);
  TEST_ASSERT_EQUAL_STRING("/stories/monsoon_cloud/s00.wav", items[0].path);
  TEST_ASSERT_EQUAL(PlayKind::Name, items[1].kind);
  TEST_ASSERT_EQUAL_STRING("/fav/f007.wav", items[1].path);
  TEST_ASSERT_EQUAL_STRING("/stories/monsoon_cloud/s01.wav", items[2].path);
  TEST_ASSERT_EQUAL_STRING("/fav/f007.wav", items[3].path);
  TEST_ASSERT_EQUAL_STRING("/stories/monsoon_cloud/s02.wav", items[4].path);
}

static void test_missing_clip_falls_back_to_default_then_chime() {
  gs::StoryCatalog cat;
  cat.add("diya", "The Little Diya", 2);
  gs::Memory m = memory_with_clip(3);  // clip file is missing from the card
  PlayItem items[gs::kMaxPlayItems];
  FakeCard card;
  card.files.insert("/names/_default.wav");
  size_t n = gs::build_playlist(*cat.get(0), m, 3, CaptureMode::ChildVoiceClip, card_exists, &card, items, 8);
  TEST_ASSERT_EQUAL(3, n);
  TEST_ASSERT_EQUAL(PlayKind::Name, items[1].kind);
  TEST_ASSERT_EQUAL_STRING("/names/_default.wav", items[1].path);
  FakeCard empty;
  n = gs::build_playlist(*cat.get(0), m, 3, CaptureMode::ChildVoiceClip, card_exists, &empty, items, 8);
  TEST_ASSERT_EQUAL(PlayKind::Chime, items[1].kind);
  TEST_ASSERT_EQUAL_STRING("", items[1].path);
}

static void test_parent_web_uses_pack_name_clip() {
  gs::StoryCatalog cat;
  cat.add("tea_garden", "The Tea Garden Elephant", 2);
  FakeKv kv;
  gs::MemoryStore st(kv, gs::MemoryLimits(), gs::Memory());
  st.load();
  uint8_t id = 0;
  st.add_name("Anya Rao", &id);
  FakeCard card;
  card.files.insert("/names/anya_rao.wav");
  PlayItem items[8];
  gs::Rng rng(5);
  const uint8_t hero = gs::pick_hero(st.mem(), CaptureMode::ParentWeb, card_exists, &card, rng);
  TEST_ASSERT_EQUAL(id, hero);
  const size_t n = gs::build_playlist(*cat.get(0), st.mem(), hero, CaptureMode::ParentWeb, card_exists, &card, items, 8);
  TEST_ASSERT_EQUAL(3, n);
  TEST_ASSERT_EQUAL_STRING("/names/anya_rao.wav", items[1].path);
}

static void test_hero_avoids_last_hero() {
  gs::Memory m;
  m.count = 3;
  FakeCard card;
  for (uint8_t i = 0; i < 3; ++i) {
    m.fav[i].id = uint8_t(10 + i);
    m.fav[i].flags = gs::kFavHasClip;
    char p[32];
    gs::fav_clip_path(m.fav[i].id, p, sizeof(p));
    card.files.insert(p);
  }
  gs::Rng rng(99);
  for (int i = 0; i < 100; ++i) {
    const uint8_t h = gs::pick_hero(m, CaptureMode::ChildVoiceClip, card_exists, &card, rng);
    TEST_ASSERT_NOT_EQUAL(m.last_hero, h);
    TEST_ASSERT_TRUE(h >= 10 && h <= 12);
    m.last_hero = h;
  }
}

static void test_no_favourites_means_no_hero() {
  gs::Memory m;
  FakeCard card;
  gs::Rng rng(3);
  TEST_ASSERT_EQUAL(0, gs::pick_hero(m, CaptureMode::ChildVoiceClip, card_exists, &card, rng));
}

static void test_name_cleaning_and_slugs() {
  char out[gs::kNameMax + 1];
  TEST_ASSERT_TRUE(gs::clean_name("  Mira   Bai ", out, sizeof(out)));
  TEST_ASSERT_EQUAL_STRING("Mira Bai", out);
  TEST_ASSERT_FALSE(gs::clean_name("Mira<3", out, sizeof(out)));
  TEST_ASSERT_TRUE(gs::name_equal_ci("KABIR", "kabir"));
  char slug[40];
  gs::name_slug("Anne-Marie D'Souza", slug, sizeof(slug));
  TEST_ASSERT_EQUAL_STRING("anne_marie_dsouza", slug);
  char path[64];
  gs::pack_name_path("Little Star", path, sizeof(path));
  TEST_ASSERT_EQUAL_STRING("/names/little_star.wav", path);
}

void register_story_tests() {
  RUN_TEST(test_shuffle_never_repeats_back_to_back);
  RUN_TEST(test_sequential_order_wraps);
  RUN_TEST(test_catalog_rejects_bad_entries);
  RUN_TEST(test_playlist_inserts_child_clip_between_segments);
  RUN_TEST(test_missing_clip_falls_back_to_default_then_chime);
  RUN_TEST(test_parent_web_uses_pack_name_clip);
  RUN_TEST(test_hero_avoids_last_hero);
  RUN_TEST(test_no_favourites_means_no_hero);
  RUN_TEST(test_name_cleaning_and_slugs);
}
