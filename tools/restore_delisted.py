"""掲載終了フラグ（delisted_at）の誤検知を、実チェックで洗い直して復活させる保守ツール。

死活チェックは「掲載中なのに dead 応答が返る」状況では誤って掲載終了を確定させる。
CIのIPがブロックされ、掲載中の案件でも 404 / トップへのリダイレクトが返るサイトが
これに当たる（ポイントインカム: 2026-08-22 に判明。ブロックされていないローカルから
同じURLを叩くと 200 が返る）。
掲載終了になった案件は死活チェックの母集団から外れる（store.is_visible）ため、原因を
取り除いても自動では戻らない。このツールで一度だけ洗い直す。

使い方（ブロックされていない回線＝ローカルPCから実行する）:
  python tools/restore_delisted.py --sites pointincome --dry-run  # 判定だけ表示
  python tools/restore_delisted.py --sites pointincome            # 復活させて保存

判定は死活チェック本体（crawler.linkcheck）と同じロジックを使う。alive と判定できた
案件だけ delisted_at / dead_streak を消し、dead・unknown はそのまま残す（掲載終了の
判定を緩めるツールではない）。
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from check_links import probe_final                # noqa: E402（判定手順を死活チェック本体と共有する）
from crawler import linkcheck                      # noqa: E402
from crawler import store as store_mod             # noqa: E402
from crawler.fetch import PoliteFetcher            # noqa: E402

CHECK_INTERVAL = 3.0  # check_links.py と同じ（1サイト内のリクエスト間隔）

# Windowsコンソール（cp932）での日本語出力の文字化けを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_sites_config() -> dict:
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", required=True,
                        help="対象サイトキーをカンマ区切りで指定（誤検知が起きたサイトだけを指定する）")
    parser.add_argument("--dry-run", action="store_true", help="保存せず判定サマリのみ表示")
    args = parser.parse_args()

    sites_config = load_sites_config()
    store = store_mod.load()
    targets = set(args.sites.split(","))

    deals = [d for d in store["deals"].values()
             if d["site"] in targets and d.get("delisted_at")]
    if not deals:
        print("[skip] 掲載終了フラグの立った案件はありません")
        return 0

    print(f"[start] {len(deals)}件を再チェックします（interval={CHECK_INTERVAL}s / "
          f"目安{len(deals) * CHECK_INTERVAL / 60:.0f}分）", flush=True)

    counts = Counter()
    restored = 0
    for site in sorted(targets):
        site_deals = [d for d in deals if d["site"] == site]
        if not site_deals:
            continue
        cfg = sites_config.get(site, {})
        fetcher = PoliteFetcher(interval=CHECK_INTERVAL)
        for deal in site_deals:
            # probe_final: 死活チェック本体と同じ「IDを保持したリダイレクトは1ホップ追う」判定
            outcome, final_url = probe_final(fetcher, deal["url"])
            verdict, _ = linkcheck.classify_response(
                outcome, final_url, cfg.get("dead_markers"), cfg.get("dead_title_markers"))
            counts[(site, verdict)] += 1
            if verdict == "alive":
                # 死活チェック本体と同じ復帰処理（delisted_at と dead_streak を消す）
                linkcheck.apply_result(deal, "alive", 0, "")
                restored += 1

    for site in sorted(targets):
        n = {v: counts[(site, v)] for v in ("alive", "dead", "unknown")}
        if sum(n.values()):
            print(f"  [{site}] alive{n['alive']} / dead{n['dead']} / unknown{n['unknown']}"
                  f" → 復活{n['alive']}件")

    tail = "（dry-run: 未保存）" if args.dry_run else ""
    print(f"[done] 復活 合計{restored}件{tail}")
    if not args.dry_run and restored:
        store_mod.save(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
