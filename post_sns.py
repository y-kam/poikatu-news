"""広報用SNS（X）へのデイリーダイジェスト／週次まとめ自動投稿。

使い方:
  python post_sns.py            # 日次ダイジェストを投稿（環境変数のAPIキーが必要）
  python post_sns.py --weekly   # 週次まとめを投稿（JST月曜・1週1回のみ。他は何もしない）
  python post_sns.py --dry-run  # 投稿せず本文と文字数だけ表示（--weekly と併用可）

環境変数: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET
- 新着0件・APIキー未設定時は何もせず正常終了する（ワークフローを止めない）
- 1日複数回実行に対応: data/sns_state.json の posted_today に当日の投稿済み案件キーを
  記録し、未投稿の新着があるときだけ投稿する（同じ案件を当日二度投稿しない）
- 過去の投稿との被り防止: posted_titles に投稿済み案件を「正規化タイトル→最高報酬額」で
  日をまたいで永続記録する。同一商品が別サイト・別deal_idで再登場しても、報酬が過去の
  投稿を上回らない限り再投稿しない（上回れば「お得情報の更新」として再投稿を許可）。
- 投稿の主役は「当日ポイントUP（renewed_at＝増額での再浮上）した案件」。過去最高値を
  更新したUP→増額幅の大きいUPの順に選び、枠が余ったぶんだけ初出の新着で補う。
  本文ではUPに「⤴+○円」、過去最高値の更新には「🔥最高値」を付けて区別する。
- 誘導先URLは本文の中身に合わせて切り替える。UP案件を載せた日はUP額ランキング
  （ranking.html）、それ以外はトップ。詳細は _cta を参照。

週次まとめ（--weekly）は日次とは別枠の投稿で、確定した先週分（data/weekly.json＝
weekly.html と同じスナップショット）から増額幅の大きい案件を紹介し weekly.html へ送る。
JST月曜以外・その週を投稿済みの場合はX APIを叩かずに正常終了するため、投稿枠の実行から
毎回呼んでよい（従量課金が発生するのは実際に投稿する週1回だけ）。
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 値動き系列の作り方・増額幅の文字列はサイト表示（値動き履歴の「過去最高」バッジ、
# 新着一覧のUPバッジ）と同じ関数を使う。Xとサイトで判定・表記が食い違わないようにするため。
from builder.generate import BASE_URL, _synced_entries, _up_diff, _week_span
from crawler import store as store_mod
from crawler.categorize import is_corporate, load_corporate
from crawler.normalize import normalize_title, parse_points

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

# 週次まとめ投稿で本文に載せる件数（日次と同じメダル3枠）
WEEKLY_POST_CAP = len(MEDALS)

# 「過去最高値」（🔥最高値）とみなすのに必要な値動きの観測点数。
# 観測点が2点（初回観測→今回の増額）だけの案件は、増額すれば必ず自己最高値になり
# 「過去最高」と書いても情報価値が無い（実測では直近UPの約75%がこの2点のみ）。
# 一度以上の値動きを経てなお最高値＝3点以上に限ることで、バッジの意味を保つ。
PEAK_MIN_POINTS = 3

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


def post_weight(text: str) -> int:
    """投稿本文がXで数えられる重み付き文字数。本文中のURLは実際の長さに関わらず
    一律 URL_WEIGHT として数え直す（誘導先URLの長さが掲載件数に影響しないようにするため）。"""
    weight = weighted_len(text)
    for url in re.findall(r"https?://\S+", text):
        weight += URL_WEIGHT - weighted_len(url)
    return weight


def _norm_title(title: str) -> str:
    """被り判定用のタイトルキー。サイトの名寄せ（crawler.normalize.normalize_title）を
    そのまま使い、全角/半角・大小の揺れに加えて【】（）等の注記と記号も落とす。
    「ペタペタペンギン団」「ペタペタペンギン団（多段階）（iOS）」のように注記だけが違う
    同一商品が、同じ投稿の別枠を潰したり翌日に再投稿されたりするのを防ぐため。"""
    return normalize_title(title or "")


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


def _value_series(deal: dict, history: dict) -> list:
    """案件の値動き系列（値のみ）。値動き履歴ページ（generate._history_rows）と同じく、
    最後の観測の型に合わせて円換算 or %還元のどちらかに系列を揃える。"""
    entries = _synced_entries(deal, history)
    if not entries:
        return []
    yen_type = entries[-1][1] is not None  # 円換算系列か（%還元のみの案件はFalse）
    return [v for v in ((e[1] if yen_type else e[2]) for e in entries) if v is not None]


def _is_peak(deal: dict, history: dict) -> bool:
    """観測開始以降の過去最高値を更新した案件か（PEAK_MIN_POINTS 以上の観測点があるものに限る）"""
    vals = _value_series(deal, history)
    return len(vals) >= PEAK_MIN_POINTS and vals[-1] >= max(vals)


def _up_gain(deal: dict, rates: dict) -> float:
    """今回のUPで増えた円換算額（UP案件の並び順に使う数値）。
    %還元のみ・旧値が無いなど算出できない場合は0。表示文字列は _up_diff 側で作る。"""
    old_yen, _ = parse_points(deal.get("renewed_from") or "", rates.get(deal["site"], 1.0))
    new_yen = deal.get("yen")
    if old_yen is None or new_yen is None:
        return 0.0
    return max(0.0, new_yen - old_yen)


def _up_mark(deal: dict, history: dict, rates: dict) -> str:
    """UP案件に添える増額幅・過去最高の印（例 "⤴+500円🔥最高値"）。UPでなければ空文字。"""
    diff = _up_diff(deal, rates.get(deal["site"], 1.0))
    mark = f"⤴{diff}" if diff else "⤴UP"
    return mark + ("🔥最高値" if _is_peak(deal, history) else "")


def _order_ups(ups: list[dict], history: dict, rates: dict) -> list[dict]:
    """UP案件の掲載順。観測開始以降の過去最高値を更新した案件を最優先にし、
    同じ区分内では増額幅、報酬額の大きい順にする。投稿枠が少ない日でも、
    情報価値の高い過去最高値の更新を主役として取り上げるため。"""
    return sorted(
        ups,
        key=lambda d: (
            0 if _is_peak(d, history) else 1,
            -_up_gain(d, rates),
            -_yen_of(d),
        ),
    )


def _cta(shown: list[dict], today: str, rates: dict) -> tuple[str, str]:
    """本文末尾の誘導文とURL。本文に載せた案件に合わせて飛び先を変える。

    UP案件を載せた日はUP額ランキング（ranking.html）へ直接送る。同ページは投稿と同じ
    「増額幅（円換算）の大きい順」で現在も増額中の案件を並べており、投稿の続きをそのまま
    見せられるため。ただし同ページは円換算できる増額のみが対象なので、載せたUPが%還元
    だけで増額幅を円で出せない日は、案件が並ばない恐れがあるためトップへ送る。"""
    if any(_is_up_today(d, today) and _up_gain(d, rates) > 0 for d in shown):
        return "増額中の案件はこちら👇", f"{BASE_URL}/ranking.html"
    return "最新情報はこちら👇", f"{BASE_URL}/"


def _reward_text(deal: dict) -> str:
    if deal.get("yen"):
        return f"{deal['yen']:,.0f}円分"
    return deal["points_text"]


def compose(new_deals: list[dict], today: str, site_names: dict, is_first_post: bool,
            history: dict, rates: dict) -> tuple[str, list[dict]]:
    """ダイジェスト本文と、実際に本文へ載せた案件リストを返す（280ウェイトに収まるまで
    掲載件数・タイトル長を削る）。

    掲載枠は「当日ポイントUPした案件」を主役にする（値上がりはポイ活で最も価値が高く、
    初出の新着より拡散されやすいため）。UPの並びは _order_ups（過去最高を最優先）。
    UPが3件に満たない日だけ、残り枠を従来どおりの新着（手軽さ優先→報酬額順）で補う。"""
    ups = _order_ups([d for d in new_deals if _is_up_today(d, today)], history, rates)
    fresh = sorted([d for d in new_deals if not _is_up_today(d, today)],
                   key=lambda d: (0 if _is_easy(d) else 1, -_yen_of(d)))
    top = (ups + fresh)[:len(MEDALS)]

    month_day = f"{int(today[5:7])}/{int(today[8:10])}"
    n_up, n_new = len(ups), len(fresh)
    n_peak = sum(1 for d in ups if _is_peak(d, history))
    # UPがある日はUPを見出しに立てる（無い日だけ従来の新着ダイジェストの体裁に戻す）。
    # UP日の見出しに新着件数は入れない（主題がぼやけるうえ、案件行に使える文字数が減るため）
    if n_up:
        counts_text = f"UP{n_up}件" + (f"（過去最高{n_peak}件）" if n_peak else "")
        header = (
            f"【ポイ活ポイントUP】{month_day}は{counts_text}！" if is_first_post
            else f"【ポイ活ポイントUP・続報】{month_day} さらに{counts_text}！"
        )
        lead = "過去最高を優先して紹介👀" if n_peak else "値上がり注目👀"
    else:
        counts_text = f"{n_new}件追加"
        header = (
            f"【本日のポイ活新着】{month_day}は{counts_text}！" if is_first_post
            else f"【ポイ活新着・続報】{month_day} さらに{counts_text}！"
        )
        lead = "注目👀"

    for take in range(len(top), 0, -1):
        # UP行は増額幅・最高値の印がぶら下がり従来より長くなるため、タイトル短縮の段階を細かく取る
        for title_limit in (24, 20, 16, 12):
            shown = top[:take]
            lines = [header, "", lead]
            for medal, deal in zip(MEDALS, shown):
                title = deal["title"]
                if len(title) > title_limit:
                    title = title[:title_limit] + "…"
                site = site_names.get(deal["site"], deal["site"])
                mark = _up_mark(deal, history, rates) if _is_up_today(deal, today) else ""
                lines.append(f"{medal}{title} {_reward_text(deal)}{mark}（{site}）")
            cta, url = _cta(shown, today, rates)
            lines += ["", cta, url, "#ポイ活 #ポイントサイト"]
            text = "\n".join(lines)
            if post_weight(text) <= MAX_WEIGHT:
                return text, shown
    # ここには実質到達しないが、保険として最小構成を返す（載せた案件は無し）
    return f"{header}\n{BASE_URL}/\n#ポイ活", []


def _weekly_eligible(rows: list, store: dict) -> list:
    """週次まとめのうちX投稿に載せてよい行だけを残す。確定後に掲載終了した案件（もう申込めない）
    と、年収・投資・面談など参加者が限られる案件（日次投稿と同じ RESTRICTED_RE）を外す。
    スナップショットは表示用の最小項目しか持たないため、掲載状態と獲得条件は現在のストアから引く。
    法人・事業者向け案件はスナップショット生成の時点で既に除外済み。"""
    eligible = []
    for row in rows:
        deal = store["deals"].get(f"{row['site']}:{row['deal_id']}")
        if deal and store_mod.is_visible(deal) and not _is_restricted(deal):
            eligible.append(row)
    return eligible


def compose_weekly(rows: list, week_key: str, site_names: dict) -> str:
    """週次まとめの投稿本文（280ウェイトに収まるまで掲載件数・タイトル長を削る）。
    増額幅の大きい順に紹介し、続きは週間まとめページ（weekly.html）へ送る。"""
    start, end = _week_span(week_key)
    span = f"{int(start[5:7])}/{int(start[8:10])}〜{int(end[5:7])}/{int(end[8:10])}"
    header = f"【先週のポイントUPまとめ】{span}"
    lead = "先週いちばん上がった案件👀"
    for take in range(min(len(rows), WEEKLY_POST_CAP), 0, -1):
        for title_limit in (24, 20, 16, 12):
            lines = [header, "", lead]
            for medal, row in zip(MEDALS, rows[:take]):
                title = row["title"]
                if len(title) > title_limit:
                    title = title[:title_limit] + "…"
                site = site_names.get(row["site"], row["site"])
                lines.append(
                    f"{medal}{title} {row['new_yen']:,.0f}円分⤴+{row['diff']:,.0f}円（{site}）"
                )
            lines += ["", "先週の値上がりまとめ👇", f"{BASE_URL}/weekly.html",
                      "#ポイ活 #ポイントサイト"]
            text = "\n".join(lines)
            if post_weight(text) <= MAX_WEIGHT:
                return text
    # ここには実質到達しないが、保険として最小構成を返す
    return f"{header}\n{BASE_URL}/weekly.html\n#ポイ活"


def run_weekly(dry_run: bool) -> int:
    """週次まとめの投稿。JST月曜かつその週が未投稿のときだけ投稿し、それ以外は何もせず
    正常終了する（曜日・投稿済みの判定をここで完結させ、投稿枠の実行から毎回呼べるようにする。
    スキップ時はX APIを叩かないので従量課金も発生しない）。"""
    if datetime.now(JST).weekday() != 0:
        print("[skip] 週次まとめの投稿は月曜のみ")
        return 0
    weekly = store_mod.load_weekly()
    if not weekly:
        print("[skip] 週次まとめのデータなし（サイト生成後に作られます）")
        return 0
    week_key = max(weekly)  # 直近の確定週（キーはゼロ埋めISO週なので辞書順＝時系列順）
    state = _read_state()
    if state.get("weekly_posted") == week_key:
        print(f"[skip] {week_key} は投稿済み")
        return 0

    store = store_mod.load()
    rows = _weekly_eligible(weekly[week_key], store)
    if not rows:
        print(f"[skip] {week_key} に投稿できる増額案件なし")
        return 0
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        site_names = {k: v["name"] for k, v in json.load(f).items()}

    text = compose_weekly(rows, week_key, site_names)
    print(f"--- 週次まとめ投稿本文（weight={post_weight(text)}） ---")
    print(text)
    if dry_run:
        return 0
    if not _has_api_keys():
        print("[skip] X APIキー未設定（Secrets登録後に有効化されます）")
        return 0

    tweet_id = post_to_x(text)
    # 投稿済みの週を記録して、同じ週に投稿枠が複数回回っても二重投稿しないようにする
    state["weekly_posted"] = week_key
    state["last_weekly_tweet_id"] = tweet_id
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] 週次まとめ投稿完了: https://x.com/i/status/{tweet_id}")
    return 0


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


def _read_state() -> dict:
    """状態ファイル（data/sns_state.json）の生の内容を返す。日次投稿と週次投稿が同じファイルを
    共有するため、書き戻すときは必ずこの内容へ上書きし、他方のキー（weekly_posted 等）を
    消さないこと（消えると二重投稿につながる）。"""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def _has_api_keys() -> bool:
    """X APIの認証情報が揃っているか（未設定なら投稿せずスキップする）"""
    return all(os.environ.get(k) for k in
               ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"))


def _load_state(store: dict, today: str) -> tuple[set[str], dict]:
    """状態ファイルを読み、(当日の投稿済みキー集合, 永続タイトル履歴) を返す。
    旧形式（posted_keys / 日次リセット）からの移行にも対応する。"""
    state = _read_state()

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

    # 履歴のキーは _norm_title 由来。名寄せルールを強めると過去のキーが引けなくなり
    # 投稿済み案件を再投稿してしまうため、読み込み時に現行ルールで振り直す
    # （normalize_title は冪等なので、既に現行ルールのキーはそのまま残る）。
    # 同じキーに畳まれた記録は報酬額が最大のものを残す（_remember_title と同じ基準）。
    migrated: dict = {}
    for key, rec in posted_titles.items():
        norm = _norm_title(key)
        current = migrated.get(norm)
        if current is None or rec.get("yen", 0) > current.get("yen", 0):
            migrated[norm] = rec

    return posted_today, _prune_titles(migrated, today)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--weekly", action="store_true",
                        help="週次まとめを投稿する（JST月曜・1週1回のみ。他は何もしない）")
    args = parser.parse_args()

    if args.weekly:
        return run_weekly(args.dry_run)

    today = datetime.now(JST).strftime("%Y-%m-%d")
    store = store_mod.load()
    history = store_mod.load_history()  # 「過去最高値」判定に使う値動き履歴
    posted_today, posted_titles = _load_state(store, today)

    # 法人・事業者向け案件の判定は config/corporate.json（サイト表示の法人トグルと同じ設定）。
    # 表示時に毎回判定する仕組みでstoreには持たないため、ここでも読み込んで判定する。
    corp = load_corporate()

    # 当日初出（またはポイントUP再浮上）・表示対象・当日未投稿・属性制限なし・法人向けでない・
    # 過去投稿と被らない案件を抽出。
    eligible = [
        d for d in store["deals"].values()
        if (d["first_seen"] == today or _is_up_today(d, today))
        and store_mod.is_visible(d)  # title有・非seed・非掲載終了
        and f"{d['site']}:{d['deal_id']}" not in posted_today  # 当日投稿済みは除外
        and not _is_restricted(d)  # 属性制限系（年収○○以上など）は投稿対象外
        and not is_corporate(d, corp)  # 法人・事業者向けはフォロワーの大半が申込めないため投稿しない
        and not _already_posted(d, posted_titles)  # 過去の投稿と被る案件は除外（報酬増は許可）
    ]
    if not eligible:
        print("[skip] 未投稿の新着なし")
        return 0

    # 同一バッチ内で別サイト重複掲載された同一商品は1件に集約する。
    new_deals = _dedupe_by_title(eligible)

    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as f:
        sites_config = json.load(f)
    site_names = {k: v["name"] for k, v in sites_config.items()}
    rates = {k: v.get("rate", 1.0) for k, v in sites_config.items()}  # 増額幅の円換算に使う

    text, shown = compose(new_deals, today, site_names, is_first_post=not posted_today,
                          history=history, rates=rates)
    print(f"--- 投稿本文（weight={post_weight(text)}） ---")
    print(text)

    if args.dry_run:
        return 0

    if not _has_api_keys():
        print("[skip] X APIキー未設定（Secrets登録後に有効化されます）")
        return 0

    tweet_id = post_to_x(text)

    # 当日バッチ全件を投稿済みキーに、実際に本文へ載せた案件をタイトル履歴に記録する。
    posted_today |= {f"{d['site']}:{d['deal_id']}" for d in eligible}
    for deal in shown:
        _remember_title(posted_titles, deal, today)

    # 既存の内容へ上書きする（週次投稿が記録する weekly_posted を消さないため）
    state = _read_state()
    state.update({
        "date": today,
        "posted_today": sorted(posted_today),
        "posted_titles": posted_titles,
        "last_tweet_id": tweet_id,
    })
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] 投稿完了: https://x.com/i/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
