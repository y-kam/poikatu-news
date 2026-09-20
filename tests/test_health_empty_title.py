"""タイトル取得不能（パーサ破損）の検知が、意図的な空タイトルを誤検知しないことを検証する。

げん玉は一覧が省略名（末尾"..."）しか返さないため、既知案件は title を空にして返し
store.upsert に保存済みの正式名を保たせる（crawler/sites/gendama.py）。これは取得失敗
ではないので title_kept を立てて健全性判定の母数から除く。
"""
import unittest

from crawler import health
from crawler.sites.base import Deal


def deal(title: str, title_kept: bool = False, seeded: bool = False) -> Deal:
    return Deal(site="s", deal_id="1", title=title, points_text="100pt", yen=10.0,
                percent=None, url="https://example.com/1", title_kept=title_kept,
                seeded=seeded)


class EmptyTitleTest(unittest.TestCase):
    def test_kept_titles_are_not_counted_as_failures(self):
        """保存済みの名前を残すための空タイトルは取得失敗に数えない（母数には残す）"""
        deals = [deal("", title_kept=True)] * 9 + [deal("案件A")]

        stat = health.site_stat(deals=deals)

        self.assertEqual(stat["t"], 0)
        self.assertEqual(stat["b"], 10)

    def test_broken_selector_is_still_counted(self):
        """タイトル抽出が壊れて空になった案件は従来どおり数える"""
        deals = [deal("")] * 9 + [deal("案件A")]

        stat = health.site_stat(deals=deals)

        self.assertEqual(stat["t"], 9)

    def test_kept_titles_do_not_trigger_anomaly(self):
        """げん玉と同じ構成（約7割が意図的な空タイトル）でも異常として報告しない"""
        deals = [deal("", title_kept=True)] * 7 + [deal("案件A")] * 3
        entries = [{"at": f"2026-09-17 {i:02d}:00", **health.site_stat(deals=deals)}
                   for i in range(4)]

        kinds = [a["kind"] for a in health.evaluate({"sites": {"gendama": entries}})]

        self.assertNotIn("empty_title", kinds)
