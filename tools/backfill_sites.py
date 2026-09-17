"""複数サイトの全件バックフィルを並列に走らせ、結果を deals.json とは別のファイルに溜めてから
一括で載せる（公開用）ツール。

run.py --backfill は data/deals.json を直接更新するため、長時間の取得中にCIの日次コミットと
衝突し、deals.json のテキストマージが必要になる（テキストマージは禁止＝壊れる）。本ツールは
  1. run  : サイトごとに adapter.backfill_deals を回し、取得した案件（Dealの生データ）を
            <out-dir>/<site>.json に逐次保存する（サイト単位で並列。Ctrl+Cで中断可。
            再実行すると保存済みのIDは取得済みとして飛ばし、続きから再開する）
  2. merge: 保存した案件を、実行中の作業ツリー（origin/main から作った一時worktree）の
            data/deals.json へ store.upsert_backfill で載せる（既存の可視案件は触らない）
の2段に分け、公開時は最新の origin/main に対して merge → commit → push するだけにする。

使い方:
  python tools/backfill_sites.py run --sites a,b,c --out-dir C:\\tmp\\bf   # 数分〜数十分
  cd <一時worktree> && python <repo>/tools/backfill_sites.py merge --in-dir C:\\tmp\\bf
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import store as store_mod  # noqa: E402
from crawler.sites import ADAPTER_CLASSES  # noqa: E402
from crawler.sites.base import Deal  # noqa: E402

JST = timezone(timedelta(hours=9))
SAVE_EVERY = 40  # この件数ごとにファイルへ逐次保存する（中断耐性）

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_sites_config() -> dict:
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        return json.load(f)


def _out_path(out_dir: Path, site: str) -> Path:
    return out_dir / f"{site}.json"


def _save(path: Path, deals: list[dict]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(deals, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # 書き込み途中の中断で既存ファイルを壊さない


def run_site(site: str, sites_config: dict, store: dict, out_dir: Path, cap: int) -> tuple[str, int, str]:
    """1サイトの全件バックフィルを回し、取得件数を返す（取得分はファイルへ逐次保存）。"""
    path = _out_path(out_dir, site)
    collected: list[dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    # 既に詳細（タイトル）を持つIDは再取得しない: deals.json の取得済み ＋ 前回中断までの保存分
    known = store_mod.filled_ids(store, site) | {d["deal_id"] for d in collected if d.get("title")}
    adapter = ADAPTER_CLASSES[site](sites_config[site])
    started = time.monotonic()
    got = 0
    since_save = 0
    err = ""
    try:
        for batch in adapter.backfill_deals(known, cap):
            for d in batch:
                if d.title:
                    collected.append(asdict(d))
                    got += 1
                    since_save += 1
            if since_save >= SAVE_EVERY:
                _save(path, collected)
                since_save = 0
                print(f"  [{site}] {got}件… ({time.monotonic() - started:.0f}s)", flush=True)
    except Exception as e:  # 1サイトの失敗で他サイトを止めない（取得分は保存する）
        err = f"{type(e).__name__}: {e}"
    _save(path, collected)
    return site, got, err


def cmd_run(args) -> int:
    sites_config = _load_sites_config()
    store = store_mod.load()  # 読み取り専用（取得済みIDの参照のみ）
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = args.sites.split(",")
    unknown = [s for s in targets if s not in ADAPTER_CLASSES]
    if unknown:
        print(f"[error] アダプタ未実装: {', '.join(unknown)}")
        return 1
    print(f"[run] {len(targets)}サイトを並列にバックフィル（上限{args.cap or '無制限'}件/サイト）→ {out_dir}",
          flush=True)
    results = []
    try:
        with ThreadPoolExecutor(max_workers=len(targets)) as ex:
            for site, got, err in ex.map(
                    lambda s: run_site(s, sites_config, store, out_dir, args.cap), targets):
                results.append((site, got, err))
                print(f"[{'fail' if err else 'ok'}] {site}: 新規{got}件 {err}", flush=True)
    except KeyboardInterrupt:
        print("\n[stop] 中断しました（各サイトの取得分は保存済み。同じコマンドで再開できます）")
        return 130
    return 0


def cmd_merge(args) -> int:
    in_dir = Path(args.in_dir)
    files = sorted(in_dir.glob("*.json"))
    if not files:
        print(f"[skip] {in_dir} に取得結果がありません")
        return 1
    # merge はカレントの作業ツリー（一時worktree）の data/deals.json に対して行う
    store_mod.DATA_FILE = Path.cwd() / "data" / "deals.json"
    if not store_mod.DATA_FILE.exists():
        print(f"[error] {store_mod.DATA_FILE} がありません（リポジトリのルートで実行してください）")
        return 1
    store = store_mod.load()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    total = 0
    for path in files:
        deals = [Deal(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
        added = store_mod.upsert_backfill(store, deals, today)
        total += added
        print(f"  [{path.stem}] 取得{len(deals)}件 → 新たに掲載{added}件")
    store_mod.save(store)
    print(f"[done] 合計{total}件を {store_mod.DATA_FILE} に反映")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="サイトごとに全件取得して <out-dir>/<site>.json に保存")
    p_run.add_argument("--sites", required=True, help="対象サイトキー（カンマ区切り）")
    p_run.add_argument("--out-dir", required=True)
    p_run.add_argument("--cap", type=int, default=0, help="1サイトあたりの新規取得上限（0で無制限）")
    p_run.set_defaults(func=cmd_run)
    p_merge = sub.add_parser("merge", help="保存した案件をカレントの data/deals.json に載せる")
    p_merge.add_argument("--in-dir", required=True)
    p_merge.set_defaults(func=cmd_merge)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
