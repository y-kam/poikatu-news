"""サーバのクリック計測（click.php）から集計JSONを取得して data/clicks.json を更新する。

デイリークロール（run.py の非 --generate-only 実行）から呼ばれ、取得結果はクロール結果と
一緒にコミットされる。サイト生成（人気案件ランキング）はコミット済みの data/clicks.json を
読むだけなので、取得に失敗しても前回分でランキングを出し続けられる。
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CLICKS_FILE = ROOT / "data" / "clicks.json"
EXPORT_URL = "https://poikatu-news.com/click.php?export=1"
MAX_BYTES = 2_000_000  # 想定外に巨大な応答は取り込まない（改ざん・障害対策）
KEEP_DAYS = 35  # 手元に残す日数（click.php 側の保持日数と同じ値にする）


def fetch_clicks(timeout: int = 20) -> bool:
    """集計を取得して data/clicks.json を更新する。成功なら True。
    サーバ側でデータが失われた場合に備え、既存ファイルと日単位でマージする
    （同じ日はサーバ値を正とし、古い日は KEEP_DAYS 分だけ残す）。"""
    try:
        res = requests.get(EXPORT_URL, timeout=timeout)
        res.raise_for_status()
        if len(res.content) > MAX_BYTES:
            raise ValueError(f"応答が大きすぎます({len(res.content)}バイト)")
        days = res.json().get("days")
        if not isinstance(days, dict):
            raise ValueError("応答の形式が想定外です")
    except Exception as e:  # ネットワーク断・PHP未設置等。生成は前回分で継続できるため止めない
        print(f"[warn] クリック集計の取得に失敗（前回分を使用）: {type(e).__name__}: {e}")
        return False
    current = {}
    if CLICKS_FILE.exists():
        try:
            current = json.loads(CLICKS_FILE.read_text(encoding="utf-8")).get("days", {})
        except (OSError, json.JSONDecodeError):
            current = {}
    current.update(days)
    merged = {d: current[d] for d in sorted(current)[-KEEP_DAYS:]}
    CLICKS_FILE.write_text(
        json.dumps({"days": merged}, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    print(f"[ok] クリック集計を更新: {sum(len(v) for v in merged.values() if isinstance(v, dict))}キー/{len(merged)}日分")
    return True
