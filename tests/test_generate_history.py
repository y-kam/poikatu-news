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


class HistoryRowsCapTest(unittest.TestCase):
    """値動き履歴ページの掲載上限は区分（過去最高/値上がり/値下がり）に関係なく
    変動日の新しい順で切る（区分順に切ると値下がりが全滅する回帰の防止）。"""

    def test_cap_keeps_recent_rows_across_groups(self):
        from unittest import mock
        from builder.generate import _history_rows

        deals, history = [], {}
        # 古い日付の「過去最高」を上限より多く用意し、値下がりは最新日付で1件だけ置く
        for i in range(5):
            d = dict(deal(yen=200), deal_id=str(i))
            deals.append(d)
            history[f"test:{i}"] = [["2026-08-01", 100, None], ["2026-08-02", 200, None]]
        down = dict(deal(yen=100), deal_id="down")
        deals.append(down)
        history["test:down"] = [["2026-08-01", 200, None], ["2026-08-10", 100, None]]

        with mock.patch("builder.generate.HISTORY_PAGE_CAP", 3):
            rows, total = _history_rows(deals, history)

        self.assertEqual(total, 6)
        self.assertEqual(len(rows), 3)
        # 最新の値下がりは残り、表示順は区分（過去最高→値下がり）を保つ
        self.assertFalse(rows[-1]["up"])
        self.assertTrue(all(r["peak"] for r in rows[:-1]))
