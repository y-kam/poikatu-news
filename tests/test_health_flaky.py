"""取得成功率の低下（失敗が常態化してベースラインごと沈んだサイト）の検知を検証する。"""
import unittest

from crawler import health


def entries(fetched: list, cat: bool = True) -> list:
    """f の並びから実績履歴を作る（b/u/t は判定に影響しない値で埋める）"""
    return [{"at": f"2026-08-{20 + i // 6:02d} {i % 6:02d}:00", "f": f, "b": f,
             "u": 0, "t": 0, "err": None, "cat": cat}
            for i, f in enumerate(fetched)]


def kinds(metrics: dict, site: str) -> list:
    return [a["kind"] for a in health.evaluate(metrics) if a["site"] == site]


class FlakyTest(unittest.TestCase):
    def test_chronic_zero_is_critical_even_without_baseline(self):
        """成功率が低いままだと中央値が0になり zero/drop は反応しない。それでも検知する"""
        metrics = {"sites": {"s": entries([0, 0, 30, 0, 0, 0, 0, 30, 0, 0, 0, 0])}}

        anomalies = [a for a in health.evaluate(metrics) if a["kind"] == "flaky"]

        self.assertEqual([a["severity"] for a in anomalies], ["critical"])
        self.assertIn("成功率17%", anomalies[0]["detail"])
        self.assertNotIn("zero", kinds(metrics, "s"))  # ベースライン0で従来判定は沈黙する

    def test_healthy_site_is_not_flagged(self):
        metrics = {"sites": {"s": entries([30] * 11 + [0])}}

        self.assertNotIn("flaky", kinds(metrics, "s"))

    def test_diff_adapter_zero_is_normal(self):
        """差分取得型（cat=False）は新着が無い回の0件が正常なので対象外"""
        metrics = {"sites": {"s": entries([0] * 11 + [3], cat=False)}}

        self.assertNotIn("flaky", kinds(metrics, "s"))

    def test_recovered_site_is_cleared_by_success_streak(self):
        """対策後に取れ続けているサイトは、窓に修正前の失敗が残っていても報告しない"""
        metrics = {"sites": {"s": entries([0] * 9 + [30, 30, 30])}}

        self.assertNotIn("flaky", kinds(metrics, "s"))

    def test_recovery_streak_must_be_unbroken(self):
        """連続成功が途切れていれば（＝まだ断続的に落ちる）報告は続く"""
        metrics = {"sites": {"s": entries([0] * 9 + [30, 30, 0])}}

        self.assertIn("flaky", kinds(metrics, "s"))

    def test_short_history_is_not_judged(self):
        """窓に満たない履歴（導入直後・サイト追加直後）では判定しない"""
        metrics = {"sites": {"s": entries([0] * 6)}}

        self.assertNotIn("flaky", kinds(metrics, "s"))


if __name__ == "__main__":
    unittest.main()
