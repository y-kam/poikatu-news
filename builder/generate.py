"""クロール結果から静的サイト（site/）を生成する。"""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from crawler.categorize import (
    classify,
    is_corporate,
    load_categories,
    load_corporate,
    load_pattern_set,
    matches_patterns,
    propagate_categories,
    required_income_man,
    required_yen,
)
from crawler.merge import group_deals
from crawler.normalize import normalize_points_text, parse_points
from crawler.store import NEW_DAYS, RECENT_DAYS, load_history, recent_visible

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "site"
STATIC_DIR = ROOT / "builder" / "static"  # .htaccess / robots.txt / 404.html など
# サイト絞り込みチップに表示する各サイトのロゴ（favicon）。builder/fetch_logos.py で取得・同梱する。
LOGOS_DIR = STATIC_DIR / "logos"
BASE_URL = "https://poikatu-news.com"

JST = timezone(timedelta(hours=9))

# 「複数サイト掲載中」の比較セクションをHTMLに直接描画する上限。全件バックフィルで
# 同名案件が大量に一致してもHTMLが肥大化しないよう頭打ちにする（超過分は「全案件」で検索可能）。
MULTI_GROUP_CAP = 200

# 獲得条件テキストの表示・配信用の最大長。ポイ活倶楽部など数千字に及ぶ条件をそのまま
# 出すとHTML・deals.jsonが肥大化し表示も冗長になるため頭打ちにする（カテゴリ分類は
# 切り詰め前の全文に対して行うため、この短縮は分類精度に影響しない）。
COND_DISPLAY_MAX = 100

# 「新着」とみなす初出/再浮上からの日数は crawler/store.py の NEW_DAYS（upsert の
# 一覧バッジ再新着ルールと同一基準にするため store 側で一元定義し、ここでインポートする）

# 新着セクションをHTMLに直接描画する上限（新着順の上位N件）。比較セクションのMULTI_GROUP_CAP
# と同様、件数が多い日にindex.htmlが肥大化して転送量が増えるのを防ぐ。超過分は「新着」チップ
# の総数には数え、「全案件」の新着フィルタ（deals.json・クライアント側）で閲覧できる。
NEW_DISPLAY_CAP = 200

# UP額ランキングページ（ranking.html）の対象期間（直近日数）と掲載上限
UP_RANKING_DAYS = 7
UP_RANKING_CAP = 100

# 値動き履歴ページ（history.html）の掲載上限（変動日の新しい順の上位N件）
HISTORY_PAGE_CAP = 200

# 値動きスパークライン（インラインSVG）の描画サイズ
SPARK_W, SPARK_H = 96, 26


def _is_new(deal: dict, new_cutoff: str) -> bool:
    """自HPへの初出が直近（new_cutoff以降）かつバックフィル分でない案件か。
    バックフィルのfirst_seenは各サイト側の掲載日で自HP初出ではないため新着から除外する。"""
    return not deal.get("backfill") and deal["first_seen"] >= new_cutoff


def _is_up(deal: dict, new_cutoff: str) -> bool:
    """直近に再浮上（renewed_at）し、かつ増額幅（up_diff）を提示できる案件か。
    renewed_at は自HP側での観測日時なので、バックフィル案件も対象にする。
    サイト側バッジ検知のみで旧値（renewed_from）が無い案件は、いくら増えたか示せず
    「ポイントUP」表示が誤解を招くため、UP扱い（バッジ・新着への再浮上）にしない。
    renewed_at のデータ自体は保持し、表示層でのみ絞る（旧値を取得できるようになれば
    条件を満たして自動的に表示対象へ戻る）。up_diff は generate() 冒頭で全件に付与済み。"""
    return bool(deal.get("up_diff")) and (deal.get("renewed_at") or "")[:10] >= new_cutoff


def _is_new_or_up(deal: dict, new_cutoff: str) -> bool:
    """新着セクション・新着チップ・deals.json の new フラグの対象か（初出 or 再浮上が直近）。"""
    return _is_new(deal, new_cutoff) or _is_up(deal, new_cutoff)


def _up_diff(deal: dict, rate: float) -> "str | None":
    """ポイントUP案件の増額幅の表示文字列（例 "+500円" / "+1.5%"）。
    増額検知時に保存した旧ポイント表記（renewed_from）を現行と同じ換算率で円/%に直し、
    現在値との差を取る。サイト側バッジ検知のみで旧値が無い案件や、旧新で型が違う
    （固定pt⇄%還元の切替）案件は算出不能として None を返す（バッジのみ表示）。"""
    old_text = deal.get("renewed_from")
    if not old_text:
        return None
    old_yen, old_pct = parse_points(old_text, rate)
    new_yen, new_pct = deal.get("yen"), deal.get("percent")
    if old_yen is not None and new_yen is not None and round(new_yen - old_yen) >= 1:
        return f"+{new_yen - old_yen:,.0f}円"
    if old_pct is not None and new_pct is not None and new_pct > old_pct:
        return f"+{round(new_pct - old_pct, 2):g}%"
    return None


def _posted_at(deal: dict) -> str:
    """新着順ソート・掲載時期フィルタの基準日時。再浮上（ポイントUP）した案件は
    renewed_at を優先し、UP案件が新着の先頭に並ぶようにする。"""
    return deal.get("renewed_at") or deal.get("first_seen_at") or deal["first_seen"]


def _posted_display(deal: dict) -> str:
    """掲載/更新日時の表示文字列。時刻があれば「M/D HH:MM」、無ければ「M/D」。
    UP案件は再浮上日時（renewed_at）を表示する。"""
    at = _posted_at(deal)
    if " " in at:  # "YYYY-MM-DD HH:MM"（時刻付き）
        date_part, time_part = at.split(" ", 1)
        _, m, d = date_part.split("-")
        return f"{int(m)}/{int(d)} {time_part}"
    _, m, d = at[:10].split("-")  # 時刻未記録の既存分は日付のみ
    return f"{int(m)}/{int(d)}"


def _posted_sort_key(deal: dict) -> str:
    """新着順ソート用の正規化キー（"YYYY-MM-DD HH:MM"）。時刻未記録の案件は当日0:00扱い。"""
    at = _posted_at(deal)
    return at if " " in at else (at[:10] + " 00:00")


def _dedupe_priority(deal: dict) -> tuple:
    """同名重複から残す1件を選ぶ優先度。還元額（円換算→%）が大きいもの、
    同額なら自HP初出が新しいものを優先する。"""
    return (deal.get("yen") or 0, deal.get("percent") or 0, _posted_sort_key(deal))


def _dedupe_same_site(deals: list[dict]) -> list[dict]:
    """同一サイトが同じ案件名を別IDで二重掲載した重複を、案件名(タイトル)完全一致で
    1件に畳む。全案件一覧・新着・比較・件数への二重表示を防ぐ。残す1件は
    _dedupe_priority が最大のもの。データ(store)は変更せず表示・集計の母集団だけを
    対象にするため、別IDは引き続き個別にリンクチェックできる（掲載終了時に正しく消える）。"""
    best: dict[tuple, dict] = {}
    for deal in deals:
        key = (deal["site"], deal["title"])
        current = best.get(key)
        if current is None or _dedupe_priority(deal) > _dedupe_priority(current):
            best[key] = deal
    return list(best.values())


# 「更新時刻」チップの時間窓（時間）。RECENCY_FULL(72h)は全新着＝時間で絞らない。
# テンプレ側スクリプトの RECENCY_WINDOWS / FULL_HOURS と一致させること。
RECENCY_WINDOWS = (4, 8, 24, 72)
RECENCY_FULL = 72


def _age_hours(posted: str, now: datetime) -> "float | None":
    """掲載時刻(posted)から基準時刻nowまでの経過時間。クライアントの ageHours と同一基準。
    時刻付き("YYYY-MM-DD HH:MM")は実経過、日付のみは暦日差×24h（時分の粒度が無いため）。
    判定不能はNone。nowはJSTのnaive datetimeを渡す（postedもJSTのwall-clock表記のため）。"""
    if not posted:
        return None
    try:
        if len(posted) >= 16 and posted[10] in " T":  # 時刻付き "YYYY-MM-DD HH:MM"
            dt = datetime.strptime(posted[:16].replace("T", " "), "%Y-%m-%d %H:%M")
            return (now - dt).total_seconds() / 3600
        dt = datetime.strptime(posted[:10], "%Y-%m-%d")  # 日付のみ
        return max(0, (now.date() - dt.date()).days) * 24
    except (ValueError, IndexError):
        return None


def _recency_counts(new_deals: list, now: datetime) -> dict:
    """「更新時刻」チップの各時間窓に入る新着件数。カテゴリ・サイトチップと同様にサーバ側で
    初期件数を埋め込むために使う（deals.json 取得前でも正しい件数を出す）。取得後はクライアントが
    カテゴリ・サイト・獲得額の選択を反映して再計算するため、ここでは全新着に対する内訳を数える。"""
    counts = {h: 0 for h in RECENCY_WINDOWS}
    for d in new_deals:
        age = _age_hours(_posted_at(d), now)
        for h in RECENCY_WINDOWS:
            if h >= RECENCY_FULL or (age is not None and age <= h):
                counts[h] += 1
    return counts


def _short_condition(text: str) -> str:
    """獲得条件を表示用に1行へ畳み、最大長で切り詰める（超過時は末尾に「…」）。"""
    collapsed = " ".join((text or "").split())  # 改行・連続空白を1スペースに畳む
    if len(collapsed) <= COND_DISPLAY_MAX:
        return collapsed
    return collapsed[:COND_DISPLAY_MAX].rstrip() + "…"


def _slim_deal(deal: dict, new_cutoff: str) -> dict:
    """「全案件」一覧・検索用の軽量案件データ（deals.json の1件）を作る。
    表示に必要な項目だけに絞って転送量を抑える（deal_id/seeded/last_seen等は出力しない）。"""
    is_new = _is_new_or_up(deal, new_cutoff)
    slim = {
        "site": deal["site"],
        "title": deal["title"],
        "url": deal["url"],
        "points_text": deal["points_text"],
        "yen": deal.get("yen"),
        "percent": deal.get("percent"),
        "condition": _short_condition(deal.get("condition", "")),
        "category": deal["category"],
        "new": is_new,
        # 掲載時期（○時間前）フィルタ用の初出/再浮上日時。新着案件のみ出す（時期フィルタは
        # 新着時のみ効くため。非新着に付けると転送量が増えるだけ）。時刻付きが無い旧案件は
        # 日付のみになり、クライアント側で当日0:00として扱われる。
        "posted_at": _posted_at(deal) if is_new else None,
    }
    if _is_up(deal, new_cutoff):
        slim["up"] = True  # UPバッジ表示用。該当案件のみキーを出して転送量を抑える
        slim["up_diff"] = deal["up_diff"]  # 増額幅（+500円等）。_is_up が真なら算出済み
    if deal.get("corporate"):
        slim["corp"] = True  # 法人案件フラグ。既定は非表示・トグルONで表示。該当のみ出し転送量を抑える
    # 投資・入金・年収フラグとしきい値用の必要額。既定は全表示、除外トグル・しきい値でクライアントが絞る。
    # 該当キーのみ出して転送量を抑える（req_yen/income_man は抽出できた案件のみ）。
    if deal.get("invest"):
        slim["invest"] = True
    if deal.get("deposit"):
        slim["deposit"] = True
    if deal.get("income"):
        slim["income"] = True
    if deal.get("req_yen") is not None:
        slim["req_yen"] = deal["req_yen"]      # 投資・入金で用意が必要な額（円）
    if deal.get("income_man") is not None:
        slim["income_man"] = deal["income_man"]  # 年収条件で必要な年収（万円）
    return slim


def _date_md(date_str: str) -> str:
    """"YYYY-MM-DD" を「M/D」表示にする（ランキング・値動き履歴の日付用）。"""
    _, m, d = date_str[:10].split("-")
    return f"{int(m)}/{int(d)}"


def _sparkline(vals: list) -> dict:
    """値動きスパークライン（折れ線SVG）の座標を作る。xは変化点の等間隔（時間軸ではない）、
    yは最小〜最大を高さに正規化する。テンプレートで polyline / 終点の circle に使う。"""
    pad = 3
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [
        (pad + (SPARK_W - 2 * pad) * i / (len(vals) - 1),
         pad + (SPARK_H - 2 * pad) * (1 - (v - lo) / span))
        for i, v in enumerate(vals)
    ]
    return {
        "w": SPARK_W, "h": SPARK_H,
        "points": " ".join(f"{x:.1f},{y:.1f}" for x, y in pts),
        "last_x": f"{pts[-1][0]:.1f}", "last_y": f"{pts[-1][1]:.1f}",
    }


def _synced_entries(deal: dict, history: dict) -> list:
    """案件の値動き履歴を、現在値（deals.json）と突き合わせて返す。履歴末尾と現在値が
    ズレていたら現在値を最終観測日で補う（履歴の記録漏れ＝クロール異常終了やバックフィルの
    値上書き等があっても、ランキングの「増額中」判定・履歴ページの現在値が実データと
    食い違わないようにする）。比較は _reward_change と同じ優先順（円→%、型違いは比較不能）。"""
    entries = history.get(f"{deal['site']}:{deal['deal_id']}", [])
    if not entries:
        return entries
    _, last_yen, last_pct = entries[-1]
    yen, pct = deal.get("yen"), deal.get("percent")
    if yen is not None and last_yen is not None:
        synced = yen == last_yen
    elif pct is not None and last_pct is not None:
        synced = pct == last_pct
    else:
        synced = True  # 型違い等で比較不能。誤った補完を避けそのまま使う
    if synced:
        return entries
    return entries + [[deal.get("last_seen") or "", yen, pct]]


def _up_ranking_rows(recent: list, history: dict, today: str) -> list:
    """UP額ランキングの行データ。値動き履歴の円換算系列の末尾が「連続増額」で終わり、
    最後の増額が直近UP_RANKING_DAYS日内の案件を、増額幅（現在値−UP開始前の元値）の
    大きい順に返す。増額後に減額された案件は系列末尾が減額になるため自然に外れる
    （＝現在も増額中のみ）。円換算できない%還元のみの変化は対象外。"""
    cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=UP_RANKING_DAYS - 1)
    ).strftime("%Y-%m-%d")
    rows = []
    for deal in recent:
        entries = _synced_entries(deal, history)
        series = [(e[0], e[1]) for e in entries if e[1] is not None]  # 円換算の変化点のみ
        if len(series) < 2 or series[-1][1] <= series[-2][1] or series[-1][0] < cutoff:
            continue
        # 末尾の連続増額をさかのぼり、UP開始前の元値を求める（4000→8000→13000 は +9000 と数える）
        i = len(series) - 1
        while i > 0 and series[i][1] > series[i - 1][1]:
            i -= 1
        diff = series[-1][1] - series[i][1]
        if round(diff) < 1:
            continue
        rows.append({
            "site": deal["site"], "title": deal["title"], "url": deal["url"],
            "category": deal["category"], "points_text": deal["points_text"],
            "old_yen": series[i][1], "new_yen": series[-1][1], "diff": diff,
            "up_date": _date_md(series[-1][0]),
        })
    rows.sort(key=lambda r: r["diff"], reverse=True)
    return rows[:UP_RANKING_CAP]


def _history_rows(recent: list, history: dict) -> tuple[list, int]:
    """値動き履歴ページの行データ（変動日の新しい順・上限N件）と対象総数を返す。
    変化点のある案件のみ対象。系列は最後の変化点の型（円換算 or %還元）に合わせ、
    現在値が観測した中での最高なら「過去最高」バッジを付ける。"""
    rows = []
    for deal in recent:
        entries = _synced_entries(deal, history)
        if len(entries) < 2:
            continue
        yen_type = entries[-1][1] is not None  # 円換算系列か（%還元のみの案件はFalse）
        series = [(e[0], e[1] if yen_type else e[2]) for e in entries
                  if (e[1] if yen_type else e[2]) is not None]
        if len(series) < 2:
            continue
        vals = [v for _, v in series]
        fmt = (lambda v: f"{v:,.0f}円") if yen_type else (lambda v: f"{round(v, 2):g}%")
        diff = vals[-1] - vals[-2]
        rows.append({
            "site": deal["site"], "title": deal["title"], "url": deal["url"],
            "category": deal["category"], "points_text": deal["points_text"],
            "yen_type": yen_type, "cur_disp": fmt(vals[-1]), "prev_disp": fmt(vals[-2]),
            "diff_disp": ("+" if diff > 0 else "−") + fmt(abs(diff)),
            "up": vals[-1] > vals[-2],
            "peak": vals[-1] >= max(vals),  # 観測開始以降の最高値（過去最高バッジ）
            "changed": _date_md(series[-1][0]),
            "changes": len(series) - 1,
            "spark": _sparkline(vals),
            "sort_key": series[-1][0],
        })
    rows.sort(key=lambda r: r["sort_key"], reverse=True)
    return rows[:HISTORY_PAGE_CAP], len(rows)


def _logo_web_path(site_key: str):
    """サイトキーに対応するロゴ（logos/{key}.*）の配信パスを返す。無ければ None。
    拡張子は取得元の形式により png/ico/jpg 等と異なり得るためグロブで探す。"""
    for path in sorted(LOGOS_DIR.glob(f"{site_key}.*")):
        return f"logos/{path.name}"  # 生成物では site/logos/ 直下に配信される
    return None


def _load_publish() -> dict:
    """公開サイト設定（GA4測定ID・問い合わせフォームURL・AdSense client等）を読む。"""
    with (ROOT / "config" / "publish.json").open(encoding="utf-8") as f:
        return json.load(f)


def generate(store: dict, sites_config: dict, today: str) -> Path:
    categories = load_categories()
    corporate = load_corporate()
    # 投資・入金・年収の横断フラグ（既定は全表示。トグル/しきい値で絞る独立軸）の検知設定。
    invest_cfg = load_pattern_set("invest.json")
    deposit_cfg = load_pattern_set("deposit.json")
    income_cfg = load_pattern_set("income.json")
    publish = _load_publish()
    # 無効化したサイト（enabled=false）はクロールだけでなく掲載も止める。
    # data/deals.json のデータ・既知IDは残す（再有効化時に全件が新着扱いになるのを防ぐ）
    enabled_sites = {k: v for k, v in sites_config.items() if v.get("enabled")}
    # storeのデータを汚さないようコピーしてからカテゴリ・法人フラグを付与する（判定は表示時に毎回行う）
    recent = [dict(d) for d in recent_visible(store, today) if d["site"] in enabled_sites]
    for deal in recent:
        # ポイント表記を表示用に正規化（桁区切り・全角記号・先頭矢印ノイズ）。copyに対して行うため
        # 保存データ(store)や円換算(yen/percent)は不変で、以降の全描画・deals.json に一貫して反映される
        deal["points_text"] = normalize_points_text(deal.get("points_text", ""))
        deal["category"] = classify(deal, categories)
        # 法人・事業者向け（個人＝一般消費者では申込めない）案件フラグ。カテゴリと独立した横断軸。
        deal["corporate"] = is_corporate(deal, corporate)
        # 投資・入金・年収の横断フラグ。既定は全表示で、除外トグル・しきい値で絞る独立軸。
        deal["invest"] = matches_patterns(deal, invest_cfg)
        deal["deposit"] = matches_patterns(deal, deposit_cfg)
        deal["income"] = matches_patterns(deal, income_cfg)
        # しきい値用の必要額。該当種別のみ算出し、抽出できなければ None（しきい値では隠さない）。
        deal["req_yen"] = required_yen(deal) if (deal["invest"] or deal["deposit"]) else None
        deal["income_man"] = required_income_man(deal) if deal["income"] else None
    # キーワードで「その他」になった案件は、同一案件の他サイト掲載の分類結果を流用して救済
    propagate_categories(recent, categories)
    # 同一サイトが同じ案件名を別IDで二重掲載した重複を1件に畳む（新着・比較・全案件・件数の
    # すべてに反映させるため、グルーピングや集計より前段で実施する）
    recent = _dedupe_same_site(recent)
    # 増額幅（+500円等）を先に全件へ付与する（renewed_from の無い案件は None）。
    # UP表示の判定（_is_up＝増額幅を提示できる再浮上のみUP扱い）とバッジ横の表示の両方で使う
    for deal in recent:
        deal["up_diff"] = _up_diff(deal, enabled_sites[deal["site"]]["rate"])

    # 法人案件を除いた「個人向け」母集団。既定表示（法人トグルOFF）の件数・ランキング・値動き履歴・
    # サーバ埋め込みの各チップ件数はこちらを基準にする。トップの新着/比較/全案件のDOM・deals.json には
    # 法人案件も corp フラグ付きで載せ、「法人案件も含む」トグルON時にクライアント側で表示・再集計する。
    personal = [d for d in recent if not d["corporate"]]
    has_corporate = len(personal) < len(recent)
    # 投資・入金・年収の除外トグルは、該当案件が表示データにある日だけ出す（無ければボタンを出さない）。
    # 既定は全表示のため recent（法人含む全表示母集団）を基準に有無を判定する。
    has_invest = any(d["invest"] for d in recent)
    has_deposit = any(d["deposit"] for d in recent)
    has_income = any(d["income"] for d in recent)

    # 値動き履歴（変化点ログ）。UP額ランキング・値動き履歴ページのデータ源。
    # これらのページはトグルを持たないため、法人案件を除いた personal のみを対象にする（全ページで非表示に統一）。
    history = load_history()
    ranking_rows = _up_ranking_rows(personal, history, today)
    history_rows, history_total = _history_rows(personal, history)

    # 「新着」の下限日（直近NEW_DAYS日・本日含む）。以降に自HP初出した案件を新着扱いにする
    new_cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=NEW_DAYS - 1)
    ).strftime("%Y-%m-%d")

    groups = group_deals(recent)
    for g in groups:
        g["category"] = g["deals"][0]["category"]  # 最高還元案件のカテゴリを代表にする
        # サイト絞り込み用。グループは複数サイトを含むためスペース区切りで全キーを持たせる
        g["site_keys"] = " ".join(dict.fromkeys(d["site"] for d in g["deals"]))
        # 「新着」チップ用。直近の新着掲載（初出 or 再浮上）を1件でも含むグループを新着扱いにする
        g["has_new"] = any(_is_new_or_up(d, new_cutoff) for d in g["deals"])
        # 法人フラグ（同名案件のグループなので全掲載で一致）。既定は非表示・トグルONで表示する
        g["corporate"] = any(d["corporate"] for d in g["deals"])
        # 投資・入金・年収フラグと、しきい値用の必要額（同名案件なので原則一致。安全側に最大を採る）
        g["invest"] = any(d["invest"] for d in g["deals"])
        g["deposit"] = any(d["deposit"] for d in g["deals"])
        g["income"] = any(d["income"] for d in g["deals"])
        req = [d["req_yen"] for d in g["deals"] if d.get("req_yen") is not None]
        g["req_yen"] = max(req) if req else None
        inc = [d["income_man"] for d in g["deals"] if d.get("income_man") is not None]
        g["income_man"] = max(inc) if inc else None
    multi_all = [g for g in groups if g["sites"] >= 2]
    multi_truncated = len(multi_all) > MULTI_GROUP_CAP
    multi_groups = multi_all[:MULTI_GROUP_CAP]
    # 新着セクション（初出＋ポイントUP再浮上）。掲載/再浮上日時の降順＝新着順で並べ、
    # 同時刻は還元額の多い順。DOMには法人案件も含めて描画し（トグルONで表示）、
    # 「新着」チップ件数・件数系は既定表示に合わせ personal（法人除外）で数える。
    new_all = sorted(
        (d for d in recent if _is_new_or_up(d, new_cutoff)),
        key=lambda d: (_posted_sort_key(d), d.get("yen") or 0),
        reverse=True,
    )
    new_personal = [d for d in new_all if not d["corporate"]]
    new_total = len(new_personal)  # 「新着」チップの総数（既定＝法人除外。トグルONで再集計）
    new_truncated = new_total > NEW_DISPLAY_CAP
    new_deals = new_all[:NEW_DISPLAY_CAP]  # HTMLに描画するのは新着順の上位N件（法人はDOMに残しトグルで開閉）
    # ポイントUP（再浮上）案件も新着セクションにUPバッジ＋増額幅付きで載せる
    # （専用セクションは持たない。新着チップの件数・絞り込みと整合させるため新着扱い）
    for d in new_deals:
        d["posted"] = _posted_display(d)     # テンプレートで表示する掲載/更新日時文字列
        d["posted_at"] = _posted_at(d)       # data-posted-at（掲載時期フィルタ）用
        d["up"] = _is_up(d, new_cutoff)      # UPバッジ表示（増額幅up_diffは付与済み）
        d["posted_label"] = "更新" if d["up"] else "掲載"

    # サイトの最終更新時刻（JST）。テンプレのUPDATED_ATと「更新時刻」チップの件数計算で同一値を
    # 使うため一度だけ取得し、クライアントと粒度を合わせて分単位に丸める。
    now_jst = datetime.now(JST).replace(second=0, microsecond=0)
    updated_at = now_jst.strftime("%Y-%m-%d %H:%M")
    # 構造化データ(dateModified)・sitemap の lastmod 用のISO8601表記（例: 2026-07-09T13:45+09:00）。
    # 検索エンジン・AIに「いつ更新されたか」を機械可読で伝える鮮度シグナル。
    updated_at_iso = now_jst.isoformat(timespec="minutes")
    # 「更新時刻」チップの初期件数。他チップ同様サーバ側で埋め込み、deals.json 取得前でも
    # 0件のまま固まらないようにする（取得後はクライアントが選択を反映して再計算する）。
    # 既定表示に合わせ法人案件を除いた new_personal で数える（トグルON時はクライアントが再計算する）。
    recency_counts = _recency_counts(new_personal, now_jst.replace(tzinfo=None))

    # カテゴリ×サイトの件数マトリクス（キー""は「すべて」、"_new"は本日初出の擬似カテゴリ）。
    # チップの初期件数と、絞り込み時にもう片方のチップ件数を書き換えるクライアント処理の
    # 両方をここから導出し、表示件数が食い違わないようにする
    # 既定表示（法人トグルOFF）に合わせ、チップ初期件数は personal（法人除外）で数える。
    # トグルON時は refreshChipCounts が deals.json（法人含む）から再集計する。
    counts_matrix = {}
    for deal in personal:
        cats = ["", deal["category"]] + (["_new"] if _is_new_or_up(deal, new_cutoff) else [])
        for cat in cats:
            row = counts_matrix.setdefault(cat, {})
            for site_key in ("", deal["site"]):
                row[site_key] = row.get(site_key, 0) + 1
    counts = {cat: row[""] for cat, row in counts_matrix.items()}  # カテゴリ別合計
    site_counts = counts_matrix.get("", {})  # サイト別合計（キー""は全体件数）
    # 同一キーの多段ルール（優先度違い）があるためキーで重複排除する
    category_chips, seen = [], set()
    for c in categories:
        if counts.get(c["key"]) and c["key"] not in seen:
            seen.add(c["key"])
            category_chips.append({"key": c["key"], "name": c["name"], "count": counts[c["key"]]})
    category_names = {c["key"]: c["name"] for c in categories}

    # recentはenabledサイトに絞り込み済みのため、チップもenabledのみでよい。
    # logoはロゴを取得できたサイトのみ付与（無いサイトはテンプレートでサイト名表示にフォールバック）
    site_chips = [
        {"key": key, "name": s["name"], "count": site_counts[key], "logo": _logo_web_path(key)}
        for key, s in enabled_sites.items()
        if site_counts.get(key)
    ]

    env = Environment(
        loader=FileSystemLoader(ROOT / "builder" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["yen"] = lambda v: f"{v:,.0f}" if v else ""
    env.filters["cond"] = _short_condition  # 獲得条件を表示用に短縮（比較表のcond）

    # 景表法（ステマ規制）対応のPR表記は、広告・紹介リンクを実際に掲載している時だけ出す。
    # referral_url 設定 or AdSense 設定を検知して自動でON/OFFし、収益化開始時の戻し忘れを防ぐ。
    has_referral = any(s.get("referral_url") for s in enabled_sites.values())
    has_promotion = has_referral or bool(publish.get("adsense_client_id"))

    # 全ページ共通のコンテキスト（base.html.j2 が参照する）
    common = {
        "base_url": BASE_URL,
        "publish": publish,
        "site_count": len(enabled_sites),
        "has_referral": has_referral,
        "has_promotion": has_promotion,
    }
    html = env.get_template("index.html.j2").render(
        updated_at=updated_at,
        updated_at_iso=updated_at_iso,
        today=today,
        new_days=NEW_DAYS,
        recency_counts=recency_counts,
        sites=enabled_sites,
        new_deals=new_deals,
        new_total=new_total,
        new_truncated=new_truncated,
        multi_groups=multi_groups,
        multi_truncated=multi_truncated,
        groups=groups,
        recent_days=RECENT_DAYS,
        # 既定表示（法人トグルOFF）の全案件数。ヘッダの件数・「全て」チップ・「全案件」見出しに使う。
        # トグルON時はクライアント（refreshChipCounts / renderAll）が deals.json から件数を再計算する。
        total_recent=len(personal),
        has_corporate=has_corporate,
        has_invest=has_invest,
        has_deposit=has_deposit,
        has_income=has_income,
        category_chips=category_chips,
        category_names=category_names,
        site_chips=site_chips,
        site_names={k: v["name"] for k, v in enabled_sites.items()},
        **common,
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    # 固定ページ（運営者情報・プライバシーポリシー）。ASP・AdSense審査の前提となる
    for page in ("about", "privacy"):
        (OUTPUT_DIR / f"{page}.html").write_text(
            env.get_template(f"{page}.html.j2").render(**common), encoding="utf-8"
        )
    # UP額ランキング・値動き履歴（値動き履歴 data/history.json から毎回生成する）
    page_ctx = dict(
        updated_at=updated_at,
        updated_at_iso=updated_at_iso,
        up_days=UP_RANKING_DAYS,
        category_names=category_names,
        site_names={k: v["name"] for k, v in enabled_sites.items()},
        **common,
    )
    (OUTPUT_DIR / "ranking.html").write_text(
        env.get_template("ranking.html.j2").render(rows=ranking_rows, **page_ctx),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "history.html").write_text(
        env.get_template("history.html.j2").render(
            rows=history_rows, total=history_total, **page_ctx
        ),
        encoding="utf-8",
    )
    # クライアントサイド「全案件」一覧・検索用の軽量データ。
    # 全件バックフィルで数千〜数万件になり得るため、表示に必要な項目だけに絞って
    # 転送量を抑える（deal_id/seeded/last_seen等は出力しない）。還元額の大きい順。
    slim_deals = sorted(
        (_slim_deal(d, new_cutoff) for d in recent),
        key=lambda d: (d.get("yen") or 0),
        reverse=True,
    )
    (OUTPUT_DIR / "deals.json").write_text(
        json.dumps({"updated_at": today, "deals": slim_deals}, ensure_ascii=False),
        encoding="utf-8",
    )
    # サーバ設定・robots等の固定ファイル（.htaccess含む）とサブディレクトリ（logos/）を生成物にコピー
    for static_file in STATIC_DIR.iterdir():
        dest = OUTPUT_DIR / static_file.name
        if static_file.is_dir():
            shutil.copytree(static_file, dest, dirs_exist_ok=True)  # ロゴ等をディレクトリごと配信
        else:
            shutil.copy2(static_file, dest)
    # トップ・ランキング・値動き履歴は日次更新のため lastmod に更新時刻(ISO8601)を入れ、
    # クロール頻度の目安として changefreq/priority も付す。固定ページ(about/privacy)は
    # 内容が変わらないため lastmod は付けない。
    (OUTPUT_DIR / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{BASE_URL}/</loc><lastmod>{updated_at_iso}</lastmod>"
        "<changefreq>daily</changefreq><priority>1.0</priority></url>\n"
        f"  <url><loc>{BASE_URL}/ranking.html</loc><lastmod>{updated_at_iso}</lastmod>"
        "<changefreq>daily</changefreq><priority>0.6</priority></url>\n"
        f"  <url><loc>{BASE_URL}/history.html</loc><lastmod>{updated_at_iso}</lastmod>"
        "<changefreq>daily</changefreq><priority>0.5</priority></url>\n"
        f"  <url><loc>{BASE_URL}/about.html</loc><changefreq>monthly</changefreq><priority>0.3</priority></url>\n"
        f"  <url><loc>{BASE_URL}/privacy.html</loc><changefreq>monthly</changefreq><priority>0.3</priority></url>\n"
        "</urlset>\n",
        encoding="utf-8",
    )
    # llms.txt: 生成AI（ChatGPT / Perplexity / Claude 等）にサイトの正体・更新頻度・主要ページを
    # 簡潔なMarkdownで伝える新標準（GEO）。AIが回答内で正しく引用・要約しやすくする。
    site_count = len(enabled_sites)
    (OUTPUT_DIR / "llms.txt").write_text(
        f"""# ポイ活ニュース

> 主要ポイントサイト{site_count}社の新着案件を毎日自動収集し、同一案件を名寄せして「どのサイト経由が一番お得か」を比較できる無料のポイ活比較サイト。

- 対象: モッピー・ハピタス・ポイントインカム・ポイントタウン等の主要ポイントサイト（{site_count}社）
- 更新頻度: 毎日2回（13:00 / 20:00 JST）自動更新
- 掲載情報: 案件名・獲得ポイント（円換算）・獲得条件・掲載サイト・出典リンク
- 最終更新: {today}

## 主要ページ
- [トップページ]({BASE_URL}/): 新着案件・複数サイトの還元比較・全案件の検索/絞り込み
- [ポイントUP額ランキング]({BASE_URL}/ranking.html): 直近{UP_RANKING_DAYS}日にポイントが増額され現在も増額中の案件を増額幅（円換算）順に掲載
- [値動き履歴]({BASE_URL}/history.html): ポイント数が変動した案件の推移と過去最高値（観測開始以降）
- [運営者情報・お問い合わせ]({BASE_URL}/about.html): サイトの趣旨・掲載データの方針・連絡先
- [プライバシーポリシー]({BASE_URL}/privacy.html): アクセス解析・広告・免責事項

## データ
- [全案件データ (JSON)]({BASE_URL}/deals.json): 掲載中の全案件（案件名・サイト・獲得ポイント・円換算額・カテゴリ・新着フラグ）
""",
        encoding="utf-8",
    )
    return OUTPUT_DIR / "index.html"
