"""広報用SNS（X）へのデイリーダイジェスト自動投稿。

使い方:
  python post_sns.py            # 投稿（環境変数のAPIキーが必要）
  python post_sns.py --dry-run  # 投稿せず本文と文字数だけ表示

環境変数: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET
- 新着0件・APIキー未設定時は何もせず正常終了する（ワークフローを止めない）
- 1日複数回実行に対応: data/sns_state.json の posted_today に当日の投稿済み案件キーを
  記録し、未投稿の新着があるときだけ投稿する（同じ案件を当日二度投稿しない）
- 過去の投稿との被り防止: posted_titles に投稿済み案件を「正規化タイトル→最高報酬額」で
  日をまたいで永続記録する。同一商品が別サイト・別deal_idで再登場しても、報酬が過去の
  投稿を上回らない限り再投稿しない（上回れば「お得情報の更新」として再投稿を許可）。
- 当日ポイントUP（renewed_at＝増額・NEW再付与での再浮上）した案件も投稿対象に含める。
  本文では「⤴UP」を付けて初出の新着と区別する。
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from builder.generate import BASE_URL
from crawler import store as store_mod

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "sns_state.json"
JST = timezone(timedelta(hours=9))

# Xの文字数カウント: 全角系=2・半角=1・URLは一律23としてカウントされる
MAX_WEIGHT = 280
URL_WEIGHT = 23
MEDALS = ("🥇", "🥈", "🥉")

# 投稿済みタイトル履歴の保持日数。これを超えて再登場した商品は再投稿を許容する
# （履歴ファイルの肥大化防止と、長期間ぶりの再登場を「新情報」とみなす妥協点）。
TITLE_HISTORY_DAYS = 365

# X投稿の対象外にする案件（サイト掲載はそのまま）。
#   - 属性制限系: 年収○○以上・性別/地域限定など参加者が限られるもの
#   - 面談系: 不動産投資などの個別面談・相談。ハードルが高くフォロワー向きでない
#   - 投資系: 不動産投資・ファンド投資（実際に大金を投じる案件）
# ネット証券・FX・口座開設は「証券/FX/口座開設」表記で"投資""面談"を含まないため残る。
# 【新規】【初回購入】等のほぼ全案件に付く通常条件は除外しない。
RESTRICTED_RE = re.compile(r"年収|女性限定|男性限定|地域限定|面談|投資")

# X投稿で優先的に載せる「手軽」案件の判定（報酬額より“やりやすさ”を重視するため）。
#   手軽 = 無料・低ハードルで完了するアクション（無料会員登録/新規登録・資料請求/無料体験・
#          無料アプリDL/インストール・口座開設/年会費無料カード発行 など）を含み、かつ
#          お金や大きな手間を伴う語を一切含まないもの。
# ※「新規無料会員登録＋100万円以上投資完了」のように“無料と書いてあるが実は高ハードル”な
#   案件を確実に弾くため、EASY 語の有無だけでなく HURDLE 語の非該当も条件にするのが要点。
EASY_RE = re.compile(
    r"無料会員登録|無料登録|無料入会|新規会員登録|新規登録|会員登録"
    r"|資料請求|無料体験|無料お試し|お試し|無料モニター"
    r"|無料アプリ|アプリ(?:DL|ダウンロード|インストール)|インストール"
    r"|口座開設|カード発行|カード新規発行|クレジットカード発行"
)
HURDLE_RE = re.compile(
    r"購入|買い物|買物|ショッピング|買取|投資|出資|ファンド|課金|入金|決済"
    r"|契約|取引|回線開通|開通|有料|面談|来店|宿泊|予約|見積|査定|相談"
    r"|レベル|Level|ミッション|到達|クリア|累計|初回購入|万円以上|万以上"
)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def weighted_len(text: str) -> int:
    """Xの重み付き文字数の近似値（非ASCII=2）"""
    return sum(2 if ord(ch) > 0xFF else 1 for ch in text)


def _norm_title(title: str) -> str:
    """被り判定用にタイトルを正規化する（全角/半角・大小・空白の表記ゆれを吸収）"""
    t = unicodedata.normalize("NFKC", title or "")
    return re.sub(r"\s+", " ", t).strip().casefold()


def _yen_of(deal: dict) -> float:
    """報酬額（円）。円換算が無い案件（ポイント/%表記など）は0として扱う"""
    return deal.get("yen") or 0


def _is_restricted(deal: dict) -> bool:
    """年収・投資額・性別・地域など参加者が限られる条件付き案件か"""
    return bool(RESTRICTED_RE.search(f"{deal['title']} {deal.get('condition') or ''}"))


def _is_easy(deal: dict) -> bool:
    """報酬額より手軽さを優先するための判定。無料・低ハードルのアクションで完了し、
    購入・投資・入金など金銭/大きな手間を伴う語を含まない案件のみ True。"""
    text = f"{deal['title']} {deal.get('condition') or ''}"
    return bool(EASY_RE.search(text)) and not HURDLE_RE.search(text)


def _is_up_today(deal: dict, today: str) -> bool:
    """当日ポイントUP（増額で再浮上）した案件か。初出新着と文言を変えるための判定。
    初出も当日の案件は通常の新着として扱う。サイト表示（builder/generate._is_up）と同じく、
    旧値（renewed_from）が無くいくら増えたか示せない案件（サイト側バッジ検知のみ）は
    UP扱い・投稿対象にしない（増額幅不明のUPは誤解を招くため）。"""
    return (bool(deal.get("renewed_from"))
            and (deal.get("renewed_at") or "")[:10] == today
            and deal["first_seen"] != today)


def _already_posted(deal: dict, posted_titles: dict) -> bool:
    """過去に投稿済みで、かつ報酬が過去の投稿を上回っていない案件か（＝再投稿しない）"""
    rec = posted_titles.get(_norm_title(deal["title"]))
    if rec is None:
        return False
    return _yen_of(deal) <= rec.get("yen", 0)  # 報酬が上回れば再投稿を許可


def _remember_title(posted_titles: dict, deal: dict, date: str) -> None:
    """投稿した案件を履歴に記録する（同一タイトルは最高報酬額とその日付を保持）"""
    key = _norm_title(deal["title"])
    yen = _yen_of(deal)
    rec = posted_titles.get(key)
    if rec is None or yen > rec.get("yen", 0):
        posted_titles[key] = {"yen": yen, "date": date}


def _prune_titles(posted_titles: dict, today: str) -> dict:
    """保持日数を過ぎた投稿履歴を落とす（ファイル肥大化防止）"""
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=TITLE_HISTORY_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in posted_titles.items() if v.get("date", "") >= cutoff}


def _dedupe_by_title(deals: list[dict]) -> list[dict]:
    """同一バッチ内の同一タイトルを最高報酬版に集約する（別サイト重複掲載の吸収）"""
    best: dict[str, dict] = {}
    for d in deals:
        key = _norm_title(d["title"])
        cur = best.get(key)
        if cur is None or _yen_of(d) > _yen_of(cur):
            best[key] = d
    return list(best.values())


def _reward_text(deal: dict) -> str:
    if deal.get("yen"):
        return f"{deal['yen']:,.0f}円分"
    return deal["points_text"]


def compose(new_deals: list[dict], today: str, site_names: dict, is_first_post: bool) -> tuple[str, list[dict]]:
    """新着上位3件のダイジェスト本文と、実際に本文へ載せた案件リストを返す
    （280ウェイトに収まるまで掲載件数を減らす）"""
    # 手軽な案件を優先し、同じ手軽度どうしは報酬額の高い順。手軽が3件に満たなければ
    # 残り枠は従来どおり報酬額順の案件で補完する（手軽優先＋不足時フォールバック）。
    top = sorted(new_deals, key=lambda d: (0 if _is_easy(d) else 1, -(d.get("yen") or 0)))[:3]
    month_day = f"{int(today[5:7])}/{int(today[8:10])}"
    # 初出の新着とポイントUP（再浮上）で件数の言い回しを分ける（UPは「追加」ではないため）
    n_up = sum(1 for d in new_deals if _is_up_today(d, today))
    n_new = len(new_deals) - n_up
    if n_new and n_up:
        counts_text = f"新着{n_new}件・ポイントUP{n_up}件"
    elif n_up:
        counts_text = f"ポイントUP{n_up}件"
    else:
        counts_text = f"{n_new}件追加"
    header = (
        f"【本日のポイ活新着】{month_day}は{counts_text}！" if is_first_post
        else f"【ポイ活新着・続報】{month_day} さらに{counts_text}！"
    )

    for take in range(len(top), 0, -1):
        for title_limit in (24, 16):
            shown = top[:take]
            lines = [header, "", "注目👀"]
            for medal, deal in zip(MEDALS, shown):
                title = deal["title"]
                if len(title) > title_limit:
                    title = title[:title_limit] + "…"
                site = site_names.get(deal["site"], deal["site"])
                up_mark = "⤴UP" if _is_up_today(deal, today) else ""
                lines.append(f"{medal}{title} {_reward_text(deal)}{up_mark}（{site}）")
            lines += ["", "最新情報はこちら👇", "{URL}", "#ポイ活 #ポイントサイト"]
            text = "\n".join(lines)
            if weighted_len(text.replace("{URL}", "")) + URL_WEIGHT <= MAX_WEIGHT:
                return text.replace("{URL}", BASE_URL + "/"), shown
    # ここには実質到達しないが、保険として最小構成を返す（載せた案件は無し）
    return f"【本日のポイ活新着】{month_day}は{counts_text}！\n{BASE_URL}/\n#ポイ活", []


def post_to_x(text: str) -> str:
    """X API v2 で投稿してツイートIDを返す"""
    from requests_oauthlib import OAuth1Session

    session = OAuth1Session(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    resp = session.post("https://api.twitter.com/2/tweets", json={"text": text}, timeout=30)
    if resp.status_code != 201:
        raise RuntimeError(f"X API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["data"]["id"]


def _load_state(store: dict, today: str) -> tuple[set[str], dict]:
    """状態ファイルを読み、(当日の投稿済みキー集合, 永続タイトル履歴) を返す。
    旧形式（posted_keys / 日次リセット）からの移行にも対応する。"""
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # 当日分の投稿済みキー: 日付が変わったら空。旧キー名 posted_keys もフォールバックで読む。
    if state.get("date") == today:
        posted_today = set(state.get("posted_today", state.get("posted_keys", [])))
    else:
        posted_today = set()

    # タイトル履歴は日をまたいで永続。未保持なら旧 posted_keys から移行シードする。
    posted_titles = state.get("posted_titles")
    if posted_titles is None:
        posted_titles = {}
        seed_date = state.get("date", today)
        for key in state.get("posted_keys", []):
            deal = store["deals"].get(key)
            if deal and deal.get("title"):
                _remember_title(posted_titles, deal, seed_date)

    return posted_today, _prune_titles(posted_titles, today)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = datetime.now(JST).strftime("%Y-%m-%d")
    store = store_mod.load()
    posted_today, posted_titles = _load_state(store, today)

    # 当日初出（またはポイントUP再浮上）・表示対象・当日未投稿・属性制限なし・
    # 過去投稿と被らない案件を抽出。
    eligible = [
        d for d in store["deals"].values()
        if (d["first_seen"] == today or _is_up_today(d, today))
        and store_mod.is_visible(d)  # title有・非seed・非掲載終了
        and f"{d['site']}:{d['deal_id']}" not in posted_today  # 当日投稿済みは除外
        and not _is_restricted(d)  # 属性制限系（年収○○以上など）は投稿対象外
        and not _already_posted(d, posted_titles)  # 過去の投稿と被る案件は除外（報酬増は許可）
    ]
    if not eligible:
        print("[skip] 未投稿の新着なし")
        return 0

    # 同一バッチ内で別サイト重複掲載された同一商品は1件に集約する。
    new_deals = _dedupe_by_title(eligible)

    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        site_names = {k: v["name"] for k, v in json.load(f).items()}

    text, shown = compose(new_deals, today, site_names, is_first_post=not posted_today)
    print(f"--- 投稿本文（weight={weighted_len(text) - len(BASE_URL) - 1 + URL_WEIGHT}） ---")
    print(text)

    if args.dry_run:
        return 0

    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    if not all(os.environ.get(k) for k in keys):
        print("[skip] X APIキー未設定（Secrets登録後に有効化されます）")
        return 0

    tweet_id = post_to_x(text)

    # 当日バッチ全件を投稿済みキーに、実際に本文へ載せた案件をタイトル履歴に記録する。
    posted_today |= {f"{d['site']}:{d['deal_id']}" for d in eligible}
    for deal in shown:
        _remember_title(posted_titles, deal, today)

    state = {
        "date": today,
        "posted_today": sorted(posted_today),
        "posted_titles": posted_titles,
        "last_tweet_id": tweet_id,
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] 投稿完了: https://x.com/i/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
