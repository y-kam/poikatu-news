"""値動き履歴（data/history.json）の初期シード（一度きりの導入用スクリプト）。

履歴の記録はクロール時（store.upsert → record_history）に始まるが、導入初日から
UPランキング・値動き履歴ページに中身を出すため、gitにコミット済みの過去の
data/deals.json スナップショット（1日6回のクロールコミット）を古い順に走査し、
報酬の変化点を復元する。記録ロジックは実行時と同じ record_history を使う。

使い方:
  python seed_history.py            # 復元して data/history.json に保存
  python seed_history.py --dry-run  # 保存せず件数だけ表示

履歴が失われた場合の再構築にも使える（既存の history.json は上書きされる）。
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from crawler import store as store_mod

JST = timezone(timedelta(hours=9))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def snapshot_commits() -> list[tuple[str, str]]:
    """data/deals.json を変更したコミットの (sha, JST日付) を古い順に返す。"""
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%H %cI", "--", "data/deals.json"],
        capture_output=True, text=True, check=True,
    ).stdout
    commits = []
    for line in out.splitlines():
        sha, iso = line.split(" ", 1)
        date = datetime.fromisoformat(iso).astimezone(JST).strftime("%Y-%m-%d")
        commits.append((sha, date))
    return commits


def load_snapshot(sha: str) -> dict:
    raw = subprocess.run(
        ["git", "show", f"{sha}:data/deals.json"], capture_output=True, check=True,
    ).stdout
    return json.loads(raw)["deals"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="保存せず件数だけ表示")
    args = parser.parse_args()

    commits = snapshot_commits()
    print(f"[seed] スナップショット {len(commits)} 件を走査します")
    history: dict = {}
    prev: dict = {}
    for i, (sha, date) in enumerate(commits, 1):
        deals = load_snapshot(sha)
        for key, deal in deals.items():
            existing = prev.get(key)
            if existing is not None:
                # 実行時（upsert）と同じロジックで変化点を記録する
                d = SimpleNamespace(yen=deal.get("yen"), percent=deal.get("percent"))
                store_mod.record_history(history, key, existing, d, date)
        prev = deals
        print(f"  [{i}/{len(commits)}] {date} {sha[:8]}: 変化あり案件 {len(history)} 件")

    pruned = store_mod.prune_history(history, store_mod.load())
    changes = sum(len(v) - 1 for v in history.values())
    print(f"[seed] 復元完了: {len(history)} 案件 / 変化点 {changes} 件（掲載終了などの除外 {pruned} 件）")
    if args.dry_run:
        print("[seed] --dry-run のため保存しません")
        return 0
    store_mod.save_history(history)
    print(f"[seed] 保存しました: {store_mod.HISTORY_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
