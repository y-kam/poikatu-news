"""表示中の案件URLの生存を実チェックし、掲載終了（ページ消滅）を検知して
論理削除フラグ（delisted_at）を立てるスクリプト。

使い方:
  python check_links.py               # enabled 全サイトをチェックして保存
  python check_links.py --dry-run     # 保存せず判定サマリだけ表示
  python check_links.py --sites moppy # 指定サイトのみ（カンマ区切り）

誤削除防止:
  - 一時障害（接続不可/タイムアウト/5xx/403/429）は判定不能として据え置く
  - dead は連続 STREAK_HIGH/STREAK_LOW 回で初めて確定する
  - サイトの unknown 率が高い（全体障害の疑い）回は、そのサイトの dead を無効化する
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from crawler import linkcheck
from crawler import store as store_mod
from crawler.fetch import PoliteFetcher

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

CHECK_INTERVAL = 3.0        # 1サイト内のリクエスト間隔（GET1発なのでクロールより短め）
UNKNOWN_GUARD_RATIO = 0.5   # この割合を超えて unknown なら全体障害とみなし dead を無効化

# Windowsコンソール（cp932）での日本語出力の文字化けを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_sites_config() -> dict:
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        return json.load(f)


def probe_final(fetcher: PoliteFetcher, url: str):
    """probe した上で、案件IDを保持したままのリダイレクト（URL正規化）だけ1ホップ追い、
    (最終レスポンス or 例外, 最終URL) を返す。
    フルーツメールのように「detail?ksid=X →(301,ID保持)→ detail/?ksid=X → 404」と
    2段で掲載終了を示すサイトを最終遷移先で正しく判定するため。IDを失うリダイレクト
    （一覧・トップ等への誘導）は掲載終了の手掛かりなので追わない（従来通り3xxで判定）。"""
    try:
        resp = fetcher.probe(url)
    except Exception as e:  # 接続不可・タイムアウト等は例外オブジェクトのまま判定に渡す
        return e, url
    if 300 <= resp.status_code < 400:
        location = resp.headers.get("Location", "")
        if location and not linkcheck.redirect_is_dead(url, location):
            target = urljoin(url, location)
            try:
                return fetcher.probe(target), target
            except Exception as e:
                return e, target
    return resp, url


def check_site(site_key: str, deals: list[dict], dead_markers=None,
               dead_title_markers=None, new_markers=None) -> list[tuple]:
    """1サイト分の案件を直列に probe し (deal, verdict, threshold, site_new) のリストを返す。
    サイト単位でスレッドに割り当てる前提（PoliteFetcher/Session をスレッド間で共有しない）。
    dead_markers / dead_title_markers を渡すと、200応答でも本文/タイトルにマーカーを
    含む案件を掲載終了とみなす（掲載終了ページも200を返すソフト404サイト向け）。
    new_markers を渡すと、生存ページの本文からサイト側「NEW」表記の有無（site_new）も抽出する
    （詳細ページにしか出ないNEW表記を死活チェックの取得本文に相乗りして検知。追加リクエスト無し）。"""
    fetcher = PoliteFetcher(interval=CHECK_INTERVAL)
    results = []
    for deal in deals:
        outcome, final_url = probe_final(fetcher, deal["url"])
        verdict, threshold = linkcheck.classify_response(
            outcome, final_url, dead_markers, dead_title_markers)
        # NEW表記は生存ページの本文でのみ判定する（エラー/リダイレクト先の本文は信用しない）
        site_new = linkcheck.find_new_marker(outcome, new_markers) if verdict == "alive" else None
        results.append((deal, verdict, threshold, site_new))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="保存せず判定サマリのみ表示")
    parser.add_argument("--sites", help="対象サイトキーをカンマ区切りで指定")
    parser.add_argument("--max-workers", type=int, default=8, help="同時にチェックするサイト数")
    parser.add_argument(
        "--include-backfill", action="store_true",
        help="バックフィル案件も死活チェックする（既定は除外。全件だと外部リクエストが激増するため、"
             "手動での一括点検時のみ指定する）",
    )
    args = parser.parse_args()

    sites_config = load_sites_config()
    store = store_mod.load()
    now_dt = datetime.now(JST)
    today = now_dt.strftime("%Y-%m-%d")
    now_at = now_dt.strftime("%Y-%m-%d %H:%M")  # NEW表記遷移の再新着日時（renewed_at）用

    # 母集団: enabled サイトの表示中案件をサイト別にまとめる。
    # enabled=false のサイトは負荷の観点でチェックしない（サイト生成側の enabled
    # フィルタで表示からも除外されるため、ここで死活確認しなくても画面には出ない）。
    enabled = {k for k, v in sites_config.items() if v.get("enabled")}
    filter_sites = set(args.sites.split(",")) if args.sites else None
    targets: dict[str, list] = defaultdict(list)
    for deal in store_mod.recent_visible(store, today):
        site = deal["site"]
        if site not in enabled:
            continue
        # バックフィル案件は数千〜数万件になり得るため、既定では日次の死活チェック対象外。
        # （掲載終了リンクの掃除が必要なら --include-backfill で手動一括点検する）
        if deal.get("backfill") and not args.include_backfill:
            continue
        if filter_sites and site not in filter_sites:
            continue
        targets[site].append(deal)

    if not targets:
        print("[skip] チェック対象の案件なし")
        return 0

    total_targets = sum(len(v) for v in targets.values())
    print(f"[start] {len(targets)}サイト / {total_targets}件をチェック（interval={CHECK_INTERVAL}s）")

    # サイト単位で並列（サイト内は直列でリクエスト間隔を守る）。
    # ソフト404サイトは sites.json の dead_markers を渡して本文で死活判定する。
    # new_markers を設定したサイトはNEW表記の有無も同じ応答から抽出する。
    def run(item):
        site, deals = item
        cfg = sites_config.get(site, {})
        return (site, check_site(site, deals, cfg.get("dead_markers"),
                                 cfg.get("dead_title_markers"), cfg.get("new_markers")))

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        site_results = list(ex.map(run, targets.items()))

    newly_delisted = 0
    newly_renewed = 0
    for site, results in site_results:
        counts = Counter(verdict for _, verdict, _, _ in results)
        guard = len(results) > 0 and counts["unknown"] / len(results) > UNKNOWN_GUARD_RATIO
        applied = 0
        renewed = 0
        for deal, verdict, threshold, site_new in results:
            if guard and verdict == "dead":
                verdict = "unknown"  # 全体障害の疑い → この回は dead を確定させない
            if linkcheck.apply_result(deal, verdict, threshold, today):
                applied += 1
            # サイト側NEW表記の遷移（無→有）を再新着（ポイントUP等の再掲載）として記録する
            if linkcheck.apply_new_marker(deal, site_new, now_at):
                renewed += 1
        newly_delisted += applied
        newly_renewed += renewed
        flag = " ※全体障害の疑い→dead無効化" if guard else ""
        renew_note = f" / NEW遷移{renewed}件" if renewed else ""
        print(
            f"  [{site}] alive{counts['alive']} / dead{counts['dead']} / "
            f"unknown{counts['unknown']} → 新規掲載終了{applied}件{renew_note}{flag}"
        )

    tail = "（dry-run: 未保存）" if args.dry_run else ""
    print(f"[done] 新規掲載終了 合計{newly_delisted}件 / NEW遷移 合計{newly_renewed}件{tail}")
    if not args.dry_run:
        store_mod.save(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
