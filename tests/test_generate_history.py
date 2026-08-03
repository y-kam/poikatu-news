"""全案件向け値動き履歴の公開データを検証する。"""
import unittest

from builder.generate import _history_series, _slim_deal


def deal(*, yen=None, percent=None, last_seen="2026-08-01") -> dict:
    return {
        "site": "test",
        "deal_id": "1",
        "title": "テスト案件",
        "url": "https://example.test/deal",
        "points_text": "100pt",
        "yen": yen,
        "percent": percent,
        "condition": "",
        "category": "other",
        "first_seen": "2026-07-01",
        "last_seen": last_seen,
    }


class HistorySeriesTest(unittest.TestCase):
    def test_yen_series_is_exported(self):
        item = deal(yen=120)
        history = {"test:1": [["2026-07-01", 100, None], ["2026-08-01", 120, None]]}

        self.assertEqual(
            _history_series(item, history),
            ("yen", [("2026-07-01", 100), ("2026-08-01", 120)]),
        )
        self.assertEqual(
            _slim_deal(item, "2026-08-01", history)["history"],
            {"unit": "yen", "points": [("2026-07-01", 100), ("2026-08-01", 120)]},
        )

    def test_percent_series_is_exported(self):
        item = deal(percent=2.5)
        history = {"test:1": [["2026-07-01", None, 2.0], ["2026-08-01", None, 2.5]]}

        self.assertEqual(
            _history_series(item, history),
            ("percent", [("2026-07-01", 2.0), ("2026-08-01", 2.5)]),
        )

    def test_missing_history_does_not_add_public_key(self):
        item = deal(yen=120)

        self.assertIsNone(_history_series(item, {}))
        self.assertNotIn("history", _slim_deal(item, "2026-08-01", {}))

    def test_current_value_is_appended_when_history_lags(self):
        item = deal(yen=150, last_seen="2026-08-02")
        history = {"test:1": [["2026-07-01", 100, None], ["2026-08-01", 120, None]]}

        self.assertEqual(
            _history_series(item, history),
            ("yen", [("2026-07-01", 100), ("2026-08-01", 120), ("2026-08-02", 150)]),
        )

    def test_reward_type_switch_is_not_mixed(self):
        item = deal(yen=150)
        history = {"test:1": [["2026-07-01", 100, None], ["2026-08-01", None, 2.0]]}

        self.assertIsNone(_history_series(item, history))
        self.assertNotIn("history", _slim_deal(item, "2026-08-01", history))


if __name__ == "__main__":
    unittest.main()
