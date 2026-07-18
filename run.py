"""クロール → 差分反映 → 静的サイト生成 のオーケストレータ。

使い方:
  python run.py                     # 有効な全サイトをクロールしてサイト生成
  python run.py --sites moppy       # 指定サイトのみ
  python run.py --generate-only     # クロールせず手元のデータからサイト生成のみ
  python run.py --backfill          # 一度きり: 全案件を可能な限り取得して掲載（既定800件/サイト）
  python run.py --backfill --backfill-cap 0   # 上限なしで全件（非常に長時間）
  python run.py --purge-backfill    # バックフィルで入れた案件を一括削除して再生成

バックフィルは Ctrl+C でいつでも中断できる（それまでの取得分は保存済み）。
同じコマンドを再実行すると、取得済みをスキップして続きから再開する。
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from builder.generate import generate
from crawler import health
from crawler import store as store_mod
from crawler.sites import ADAPTER_CLASSES
from crawler.sites.base import SitemapDiffAdapter

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

# Windowsコンソール（cp932）での日本語出力の文字化けを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_sites_config() -> dict:
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        return json.load(f)


def select_targets(args, sites_config: dict) -> list:
    """クロール対象サイトを決める。--sites 明示が最優先、無ければ enabled 全サイトから
    --exclude 分を除く。--exclude はCI等で特定サイトだけ止めたいとき用
    （例: ちょびリッチはGitHub ActionsのIPがWAFブロックされるためCIでは除外し、
    ローカルの tools/crawl_chobirich.bat で取得する）。"""
    if args.sites:
        return args.sites.split(",")
    targets = [k for k, v in sites_config.items() if v.get("enabled")]
    if args.exclude:
        excluded = set(args.exclude.split(","))
        targets = [k for k in targets if k not in excluded]
    return targets


SAVE_EVERY = 40  # バックフィル中、この件数ごとに deals.json へ逐次保存する（中断耐性）
BACKFILL_STATE_FILE = ROOT / "data" / "backfill_state.json"  # 完了サイトの記録（ローカル・非コミット）


def _load_completed_sites() -> set:
    """バックフィルが完走済みのサイトキー集合を読む（再開時にスキップするため）。"""
    if BACKFILL_STATE_FILE.exists():
        try:
            return set(json.loads(BACKFILL_STATE_FILE.read_text(encoding="utf-8")).get("completed", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def _save_completed_sites(completed: set, today: str) -> None:
    BACKFILL_STATE_FILE.write_text(
        json.dumps({"completed": sorted(completed), "updated": today}, ensure_ascii=False),
        encoding="utf-8",
    )


def run_backfill(store: dict, sites_config: dict, today: str, args) -> int:
    """全案件バックフィル。サイトごとに全ページを巡回し、逐次保存しながら取り込む。

    Ctrl+C（KeyboardInterrupt）で安全に中断でき、それまでの取得分は保存済み。
    再実行すると、完走済みサイトはスキップし、中断したサイトは取得済みIDを
    飛ばして続きから再開する。--backfill-restart で完走記録を消して最初からやり直す。
    """
    if args.backfill_restart:
        BACKFILL_STATE_FILE.unlink(missing_ok=True)
    completed = _load_completed_sites()
    # --sites 明示時はユーザーが対象を選んでいるので完走スキップを適用しない
    targets = select_targets(args, sites_config)
    cap = args.backfill_cap
    print(f"[backfill] 対象{len(targets)}サイト / 上限{cap or '無制限'}件・10秒間隔 "
          "（Ctrl+Cで中断→再実行で再開）")

    for key in targets:
        if key not in ADAPTER_CLASSES:
            print(f"[skip] {key}: アダプタ未実装")
            continue
        if key in completed and not args.sites:
            print(f"[skip] {key}: 完了済み（やり直すには --backfill-restart）")
            continue
        adapter = ADAPTER_CLASSES[key](sites_config[key])
        known = store_mod.filled_ids(store, key)  # 取得済みは再取得しない（再開）
        started = time.monotonic()
        got = 0
        since_save = 0
        try:
            for batch in adapter.backfill_deals(known, cap):
                got += store_mod.upsert_backfill(store, batch, today)
                since_save += len(batch)
                if since_save >= SAVE_EVERY:
                    store_mod.save(store)
                    since_save = 0
        except KeyboardInterrupt:
            store_mod.save(store)
            print(f"\n[stop] 中断しました（{key} は{got}件まで保存済み）。"
                  "同じコマンドの再実行で続きから再開できます。")
            _finalize_backfill(store, sites_config, today)
            return 0
        except Exception as e:  # 1サイトの失敗で全体を止めない
            store_mod.save(store)
            print(f"[fail] {key}: {type(e).__name__}: {e}")
            continue
        store_mod.save(store)
        completed.add(key)
        _save_completed_sites(completed, today)
        print(f"[ok] {key}: 新規{got}件 ({time.monotonic() - started:.0f}s)")

    _finalize_backfill(store, sites_config, today)
    return 0


def _finalize_backfill(store: dict, sites_config: dict, today: str) -> None:
    store_mod.save(store)
    out = generate(store, sites_config, today)
    print(f"[ok] サイト生成: {out}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", help="対象サイトキーをカンマ区切りで指定")
    parser.add_argument("--exclude",
                        help="クロール対象から除くサイトキー（カンマ区切り。--sites指定時は無視）")
    parser.add_argument("--max-items", type=int, default=200, help="1サイトあたりの取得上限")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--backfill", action="store_true",
                        help="一度きりの全案件バックフィル（Ctrl+Cで中断・再実行で再開）")
    parser.add_argument("--backfill-cap", type=int, default=800,
                        help="バックフィル時の1サイトあたり新規取得上限（0で無制限）")
    parser.add_argument("--backfill-restart", action="store_true",
                        help="バックフィルの完走記録を消して最初のサイトからやり直す")
    parser.add_argument("--purge-backfill", action="store_true",
                        help="バックフィルで入れた案件を一括削除して再生成")
    args = parser.parse_args()

    sites_config = load_sites_config()
    store = store_mod.load()
    now_dt = datetime.now(JST)
    today = now_dt.strftime("%Y-%m-%d")
    now_at = now_dt.strftime("%Y-%m-%d %H:%M")  # 新規案件の自HP初出日時（掲載日時表示用）

    if args.purge_backfill:
        removed = store_mod.purge_backfill(store)
        store_mod.save(store)
        BACKFILL_STATE_FILE.unlink(missing_ok=True)  # 完走記録も消す（再バックフィル可能に）
        print(f"[ok] バックフィル案件を{removed}件削除しました")
        out = generate(store, sites_config, today)
        print(f"[ok] サイト生成: {out}")
        return 0

    if args.backfill:
        return run_backfill(store, sites_config, today, args)

    if not args.generate_only:
        targets = select_targets(args, sites_config)
        history = store_mod.load_history()  # 値動き履歴（既知案件の報酬変化を記録する）
        failures = []
        run_stats = {}  # サイト別の取得実績（パーサ破損検知の記録用。crawler.health が評価する）
        for key in targets:
            if key not in ADAPTER_CLASSES:
                print(f"[skip] {key}: アダプタ未実装")
                continue
            adapter = ADAPTER_CLASSES[key](sites_config[key])
            # 差分取得型（新着0件が正常なサイト）は件数ベースの0件/激減判定を無効にする
            catalog = not isinstance(adapter, SitemapDiffAdapter)
            started = time.monotonic()
            try:
                deals = adapter.fetch_deals(store_mod.known_ids(store, key), args.max_items)
            except Exception as e:  # 1サイトの失敗で全体を止めない
                print(f"[fail] {key}: {type(e).__name__}: {e}")
                failures.append(key)
                run_stats[key] = health.site_stat(error=type(e).__name__, catalog=catalog)
                continue
            new_keys = store_mod.upsert(store, deals, today, now_at, history)
            run_stats[key] = health.site_stat(deals=deals, catalog=catalog)
            seeded = sum(1 for d in deals if d.seeded)
            print(
                f"[ok] {key}: 取得{len(deals)}件 / 新規{len(new_keys)}件"
                + (f" / 初回シード{seeded}件" if seeded else "")
                + f" ({time.monotonic() - started:.0f}s)"
            )
        store_mod.save(store)
        store_mod.prune_history(history, store)  # 削除・掲載終了案件の履歴を落とす
        store_mod.save_history(history)
        # 取得実績を記録し、サイト仕様変更によるパーサ破損（0件・激減・解析不能率スパイク・例外）を
        # 過去実績と比較して検知・表示する。ここでは終了コードは変えず（＝サイト生成・デプロイは
        # 妨げない）、CI でのジョブ失敗（通知）判定は check_health.py が担う。
        metrics = health.load()
        health.record(metrics, run_stats, now_at)
        health.save(metrics)
        health.report(health.evaluate(metrics))
        if failures:
            print(f"[warn] 失敗サイト: {', '.join(failures)}", file=sys.stderr)

    out = generate(store, sites_config, today)
    print(f"[ok] サイト生成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
