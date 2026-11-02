"""リンク死活チェックの時間予算による打ち切りと、巡回位置（カーソル）での続きからの確認を検証する。"""
import time
import unittest

from check_links import check_site, next_cursor, order_for_check


def deal(deal_id: str, **over) -> dict:
    """死活チェック対象の store 行（判定に関係する項目だけ）"""
    entry = {"site": "test", "deal_id": deal_id, "url": f"https://example.test/{deal_id}"}
    entry.update(over)
    return entry


def ids(deals: list[dict]) -> list[str]:
    return [d["deal_id"] for d in deals]


class OrderForCheckTest(unittest.TestCase):
    def test_without_cursor_sorted_by_deal_id(self):
        ordered, n_priority = order_for_check([deal("3"), deal("1"), deal("2")], None)

        self.assertEqual(ids(ordered), ["1", "2", "3"])
        self.assertEqual(n_priority, 0)

    def test_resumes_after_cursor_and_wraps_around(self):
        ordered, _ = order_for_check([deal(i) for i in "12345"], "3")

        self.assertEqual(ids(ordered), ["4", "5", "1", "2", "3"])

    def test_cursor_deal_already_delisted_resumes_at_next_id(self):
        # 前回の最終チェック案件が今回の母集団から消えていても、その次の位置から続ける
        ordered, _ = order_for_check([deal("1"), deal("2"), deal("4")], "3")

        self.assertEqual(ids(ordered), ["4", "1", "2"])

    def test_suspects_always_checked_first(self):
        deals = [deal("1"), deal("2", dead_streak=1), deal("3"), deal("4")]

        ordered, n_priority = order_for_check(deals, "3")

        self.assertEqual(ids(ordered), ["2", "4", "1", "3"])
        self.assertEqual(n_priority, 1)


class NextCursorTest(unittest.TestCase):
    def test_all_checked_resets_cursor(self):
        ordered, n = order_for_check([deal(i) for i in "123"], "1")

        self.assertIsNone(next_cursor(ordered, n, 3, "1"))

    def test_cut_off_keeps_last_checked_id(self):
        ordered, n = order_for_check([deal(i) for i in "12345"], "3")

        self.assertEqual(next_cursor(ordered, n, 2, "3"), "5")

    def test_cut_off_within_suspects_keeps_old_cursor(self):
        deals = [deal("1", dead_streak=1), deal("2", dead_streak=1), deal("3")]
        ordered, n = order_for_check(deals, None)

        self.assertIsNone(next_cursor(ordered, n, 1, None))
        self.assertEqual(next_cursor(ordered, n, 2, "9"), "9")

    def test_rotation_covers_all_deals_over_runs(self):
        # 1回に2件しか確認できなくても、回を重ねれば全件を確認できる
        deals = [deal(str(i)) for i in range(1, 6)]
        cursor = None
        seen = []
        for _ in range(3):
            ordered, n = order_for_check(deals, cursor)
            seen += ids(ordered[:2])
            cursor = next_cursor(ordered, n, 2, cursor)

        self.assertEqual(set(seen), {"1", "2", "3", "4", "5"})


class CheckSiteDeadlineTest(unittest.TestCase):
    def test_past_deadline_checks_nothing(self):
        # 予算切れ後に始まったサイトはリクエストを送らずに空の結果を返す
        results = check_site("test", [deal("1"), deal("2")],
                             deadline=time.monotonic() - 1)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
