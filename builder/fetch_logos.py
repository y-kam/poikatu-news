"""各ポイントサイトのロゴ（favicon）を取得して builder/static/logos/ に同梱する一度きりのツール。

サイト絞り込みチップに表示するロゴを自前配信するため、各サイトの domain（config/sites.json）
から favicon を取得して {key}.{拡張子} として保存する。ページ表示のたびに外部へアクセスせずに
済ませる（転送量・外部依存・信頼性の観点）。

取得は次の順で試し、最初に画像が得られたものを採用する:
  1. Google favicon サービス（多くは 64x64 PNG に正規化される）
  2. サイト直下の /favicon.ico（Google に無い小規模サイト向けフォールバック）

拡張子は取得した Content-Type に合わせる（png/ico/jpg/svg/gif/webp）。配信時に正しい
MIME で返せるようにするため。どちらでも取得できなかったサイトはファイルを作らない
（テンプレート側でサイト名テキストにフォールバックする）。

使い方:
  python -m builder.fetch_logos            # 未取得のサイトだけ取得（既存はスキップ）
  python -m builder.fetch_logos --force    # 全サイトを取り直す
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITES_CONFIG = ROOT / "config" / "sites.json"
LOGOS_DIR = ROOT / "builder" / "static" / "logos"

GOOGLE_FAVICON = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
# 一部サイトは UA 無しだと弾かれ得るため通常ブラウザ相当を名乗る
UA = "Mozilla/5.0 (compatible; poikatu-logo-fetch/1.0)"

# Content-Type → 保存拡張子。未知の image/* はサブタイプから素直に導出する。
CONTENT_TYPE_EXT = {
    "image/png": "png",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/ico": "ico",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _get(url: str):
    """URLを取得し (bytes, content_type) を返す。画像でなければ None。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:  # リダイレクトは自動追従
        ctype = (resp.headers.get_content_type() or "").lower()
        data = resp.read()
    if not ctype.startswith("image/") or not data:
        return None  # 404ページ等をHTML/空で返してくるケースを弾く
    return data, ctype


def _fetch_logo(domain: str):
    """Google→サイト直下 favicon の順で試し、(bytes, ext) を返す。取れなければ None。"""
    sources = [
        GOOGLE_FAVICON.format(domain=domain),
        f"https://{domain}/favicon.ico",
    ]
    for url in sources:
        try:
            got = _get(url)
        except Exception:
            continue  # 404・接続失敗は次のソースへ
        if got:
            data, ctype = got
            ext = CONTENT_TYPE_EXT.get(ctype) or ctype.split("/")[-1].split("+")[0]
            return data, ext
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="既存ファイルがあっても取り直す")
    args = parser.parse_args()

    sites = json.loads(SITES_CONFIG.read_text(encoding="utf-8"))
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)

    saved, skipped, missing = 0, 0, []
    for key, cfg in sites.items():
        domain = cfg.get("domain")
        if not domain:
            continue
        existing = list(LOGOS_DIR.glob(f"{key}.*"))
        if existing and not args.force:
            skipped += 1
            continue
        got = _fetch_logo(domain)
        if not got:
            missing.append(f"{key}({domain})")
            continue
        data, ext = got
        for old in existing:  # 形式が変わった場合に古いファイルを残さない
            old.unlink()
        dest = LOGOS_DIR / f"{key}.{ext}"
        dest.write_bytes(data)
        saved += 1
        print(f"[ok] {key}: {domain} -> {dest.name} ({len(data)} bytes)")

    print(f"\n保存{saved}件 / スキップ{skipped}件（既存）")
    if missing:
        print("[warn] favicon を取得できず（サイト名テキストで表示されます）:")
        for m in missing:
            print(f"       - {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
