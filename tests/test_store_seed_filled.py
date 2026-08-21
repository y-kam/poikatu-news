"""初回シード（IDのみ登録）案件の表示解禁（seed_filled）の挙動を検証する。"""
import unittest

from builder.generate import _is_new
from crawler.sites.base import Deal
from crawler.store import is_catalog, is_visible, upsert


def seeded_entry(**over) -> dict:
    """初回シードでIDだけ登録された案件の store 行"""
    entry = {
        "site": "test", "deal_id": "1", "title": "", "points_text": "",
        "yen": None, "percent": None, "url": "https://example.test/1",
        "condition": "", "seeded": True,
        "first_seen": "2026-08-20", "last_seen": "2026-08-20",
    }
    entry.update(over)
    return entry


def fetched(**over) -> Deal:
    """一覧クロールで本文まで取得できた案件"""
    args = dict(site="test", deal_id="1", title="テスト案件", points_text="100pt",
                yen=100.0, percent=None, url="https://example.test/1")
    args.update(over)
    return Deal(**args)


class SeedFilledTest(unittest.TestCase):
    def test_seeded_deal_becomes_visible_when_body_is_fetched(self):
        store = {"deals": {"test:1": seeded_entry()}}

        upsert(store, [fetched()], "2026-08-21")

        entry = store["deals"]["test:1"]
        self.assertFalse(entry["seeded"])
        self.assertTrue(entry["seed_filled"])
        self.assertTrue(is_visible(entry))

    def test_filled_deal_is_catalog_and_never_shown_as_new(self):
        """シード日が直近でも「自HP初出」ではないので新着セクションには出さない"""
        store = {"deals": {"test:1": seeded_entry()}}

        upsert(store, [fetched()], "2026-08-21")

        entry = store["deals"]["test:1"]
        self.assertTrue(is_catalog(entry))
        self.assertFalse(_is_new(entry, "2026-08-19"))

    def test_seed_stub_does_not_unseal(self):
        """差分取得型のシード（本文なし）で再登録されても解禁しない"""
        store = {"deals": {"test:1": seeded_entry()}}

        upsert(store, [fetched(title="", points_text="", yen=None, seeded=True)], "2026-08-21")

        entry = store["deals"]["test:1"]
        self.assertTrue(entry["seeded"])
        self.assertFalse(is_visible(entry))

    def test_normal_deal_is_untouched(self):
        """通常の可視案件には seed_filled を付けない（新着判定を壊さない）"""
        store = {"deals": {"test:1": seeded_entry(
            seeded=False, title="テスト案件", points_text="90pt", yen=90.0)}}

        upsert(store, [fetched()], "2026-08-21")

        entry = store["deals"]["test:1"]
        self.assertNotIn("seed_filled", entry)
        self.assertTrue(_is_new(entry, "2026-08-19"))


if __name__ == "__main__":
    unittest.main()
