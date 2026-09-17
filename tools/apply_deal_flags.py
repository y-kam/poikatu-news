"""deals.json の判定フラグ（掲載終了・NEW表記）だけを別の作業ツリーへ持ち運ぶツール。

check_links.py は data/deals.json を直接更新するため、長時間の一括点検（--include-backfill。
数時間）の間にCIの日次コミットが進むと、公開時に deals.json のテキストマージが必要になる
（テキストマージは禁止＝壊れる）。本ツールは
  1. diff : 点検前のスナップショットと点検後の deals.json を比べ、判定フラグ
           （dead_streak / delisted_at / site_new / renewed_at / renewed_from）が変わった案件だけを
           JSON に書き出す
  2. apply: その JSON を、カレントの作業ツリー（origin/main から作った一時worktree）の
           data/deals.json に適用する（案件が無ければ読み飛ばす）
の2段に分け、公開時は最新の origin/main に対して apply → commit → push するだけにする。

使い方:
  copy data\\deals.json <snap>.json
  python check_links.py --include-backfill
  python tools/apply_deal_flags.py diff --before <snap>.json --out flags.json
  git checkout -- data/deals.json                     # 手元の作業ツリーは元に戻す
  cd <一時worktree> && python <repo>/tools/apply_deal_flags.py apply --flags flags.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import store as store_mod  # noqa: E402

FLAG_KEYS = ("dead_streak", "delisted_at", "site_new", "renewed_at", "renewed_from")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _flags(deal: dict) -> dict:
    return {k: deal.get(k) for k in FLAG_KEYS}


def cmd_diff(args) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))["deals"]
    after = store_mod.load()["deals"]
    changed = {}
    for key, deal in after.items():
        old = before.get(key)
        if old is None:
            continue  # 点検中に増えた案件はフラグの変化ではない
        if _flags(old) != _flags(deal):
            changed[key] = _flags(deal)  # None のキーは apply 側で削除する
    Path(args.out).write_text(json.dumps(changed, ensure_ascii=False, indent=1), encoding="utf-8")
    delisted = sum(1 for f in changed.values() if f["delisted_at"])
    streak = sum(1 for f in changed.values() if f["dead_streak"])
    print(f"[diff] フラグ変化 {len(changed)}件（掲載終了確定 {delisted} / dead 疑い {streak}）→ {args.out}")
    return 0


def cmd_apply(args) -> int:
    store_mod.DATA_FILE = Path.cwd() / "data" / "deals.json"
    if not store_mod.DATA_FILE.exists():
        print(f"[error] {store_mod.DATA_FILE} がありません（リポジトリのルートで実行してください）")
        return 1
    flags = json.loads(Path(args.flags).read_text(encoding="utf-8"))
    store = store_mod.load()
    applied = skipped = 0
    for key, values in flags.items():
        deal = store["deals"].get(key)
        if deal is None:
            skipped += 1
            continue
        for k, v in values.items():
            if v is None:
                deal.pop(k, None)
            else:
                deal[k] = v
        applied += 1
    store_mod.save(store)
    print(f"[apply] {applied}件に反映（対象外 {skipped}件）→ {store_mod.DATA_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("diff", help="スナップショットと現在の deals.json のフラグ差分を書き出す")
    p.add_argument("--before", required=True, help="点検前の deals.json のコピー")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_diff)
    p = sub.add_parser("apply", help="フラグ差分をカレントの data/deals.json に適用する")
    p.add_argument("--flags", required=True)
    p.set_defaults(func=cmd_apply)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
