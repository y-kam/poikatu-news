"""案件詳細URLの生存判定（汎用HTTP方式）。

掲載終了で消えたページ（404/410）を高確度の掲載終了、詳細ページから一覧・トップ等への
リダイレクト（案件IDが失われる遷移）を低確度の掲載終了とみなす。
一時障害（5xx/403/429/接続不可）は判定不能として据え置き、連続失敗カウンタ
（deal["dead_streak"]）で猶予を設けることで、瞬断や全体障害での誤削除を防ぐ。
"""
import re

# 掲載終了の確定に必要な連続 dead 回数（crawlと同時に1日2回実行。確度で猶予を変える）。
# 13:00/20:00 の約7時間間隔で連続 dead なら、瞬断ではなく実際の掲載終了とみなせる。
STREAK_HIGH = 2   # 404/410 のようにページ消滅が明確なケース
STREAK_LOW = 3    # リダイレクトのように曖昧なケース

_ID_RE = re.compile(r"\d{3,}")  # 全サイトの案件IDは3桁以上の数字（URL固有の識別子）
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def redirect_is_dead(url: str, location: str) -> bool:
    """リダイレクト先が掲載終了を示すか判定する。
    元URLの数字IDが遷移先URLに残っていればURL正規化（末尾スラッシュ除去等）とみなし生存、
    失われていれば一覧・トップ等への誘導＝掲載終了とみなす。"""
    if not location:
        return True  # Location欠落の3xxは異常。掲載終了寄りに扱う
    ids = _ID_RE.findall(url)
    if ids and any(i in location for i in ids):
        return False  # ID保持 → 正規化リダイレクト（例: ちょびリッチの末尾スラッシュ除去）
    return True


def _searchable_texts(result) -> list[str]:
    """マーカー照合用の本文候補を返す。
    一部サイトは Content-Type の charset 宣言が誤っており（例: 実体はUTF-8なのに
    latin-1 と宣言＝やったよ）、requests の result.text が文字化けして日本語マーカーが
    照合できない。生バイトのUTF-8デコードも候補に加えることで宣言ミスを吸収する。
    照合不能（本文取得失敗）時は空リストを返し、誤削除を避ける。"""
    texts = []
    try:
        if result.text:
            texts.append(result.text)
    except Exception:
        pass
    try:
        raw = result.content.decode("utf-8", "ignore")
        if raw and raw not in texts:
            texts.append(raw)
    except Exception:
        pass
    return texts


def has_dead_marker(result, dead_markers=None, dead_title_markers=None) -> bool:
    """応答が掲載終了/エラーページであることを示すマーカーを含むか判定する。
    ソフト404（掲載終了でも200＋トップ相当HTMLを返す）サイト向け。
      dead_markers       : 本文(HTML全体)に現れれば掲載終了とみなす文字列
                           （例: 掲載終了ページ専用のCSSファイル名＝生存本文には出ない）
      dead_title_markers : <title>内に現れれば掲載終了とみなす文字列。定型エラー文言が
                           生存ページの本文にも（隠しテンプレ等で）含まれるサイト向けに、
                           確実に死亡と分かる title に限定して誤検知を防ぐ（例: クラシル）。"""
    if not dead_markers and not dead_title_markers:
        return False
    texts = _searchable_texts(result)
    if dead_markers:
        for text in texts:
            if any(m in text for m in dead_markers):
                return True
    if dead_title_markers:
        for text in texts:
            m = _TITLE_RE.search(text)
            if m and any(tm in m.group(1) for tm in dead_title_markers):
                return True
    return False


def find_new_marker(result, new_markers) -> bool | None:
    """応答本文にサイト側の「NEW」表記マーカー（例: モッピーの <li>NEW</li>）が含まれるか。
    マーカー未設定のサイト・本文取得不能時は None（判定対象外/不能）を返す。"""
    if not new_markers:
        return None
    texts = _searchable_texts(result)
    if not texts:
        return None
    return any(m in text for text in texts for m in new_markers)


def apply_new_marker(deal: dict, found: bool | None, now: str) -> bool:
    """サイト側NEW表記の観測結果を deal に反映する。新たにNEW（再新着）へ遷移したら True。
      初観測（site_new 未記録）: ベースライン記録のみ。再新着扱いにしない
        （導入直後に、既にNEW表記が付いている既存案件が一斉にUP扱いで溢れるのを防ぐ）
      False→True: サイト側が再掲載/増額でNEW表記を付けた → renewed_at を記録（再新着）。
        ただし renew_hold（クロールで減額を観測済み。store.apply_renewal が立てる）の
        案件は次の増額まで保留する（減額された案件を新着に再浮上させない）
      True→False: NEW期間の終了 → UP記録を消す（増額前の平常状態に戻ったとみなす）"""
    if found is None:
        return False
    prev = deal.get("site_new")
    deal["site_new"] = found
    if found and prev is False and not deal.get("renew_hold"):
        deal["renewed_at"] = now
        return True
    if not found and prev is True:
        deal.pop("renewed_at", None)
        deal.pop("renewed_from", None)
    return False


def classify_response(result, url: str, dead_markers=None, dead_title_markers=None) -> tuple[str, int]:
    """probe() の戻り値（Response）または送出された例外を判定し
    (verdict, required_streak) を返す。
      verdict         : "alive" / "dead" / "unknown"
      required_streak : dead を確定するのに要する連続回数（alive/unknown時は0）
    dead_markers / dead_title_markers を渡すと、200応答でも本文/タイトルにマーカーを
    含む場合は dead と判定する（掲載終了ページも200を返すソフト404サイト向け）。"""
    if isinstance(result, Exception):
        return ("unknown", 0)  # タイムアウト・接続不可などの一時障害は判定不能
    status = result.status_code
    if 200 <= status < 300:
        # ソフト404対策: ステータスは正常でも本文/タイトルが掲載終了・エラーページなら掲載終了。
        # ページ消滅が明確な 404/410 と同等の確度なので STREAK_HIGH を用いる。
        if has_dead_marker(result, dead_markers, dead_title_markers):
            return ("dead", STREAK_HIGH)
        return ("alive", 0)
    if status in (404, 410):
        return ("dead", STREAK_HIGH)
    if 300 <= status < 400:
        location = result.headers.get("Location", "")
        return ("dead", STREAK_LOW) if redirect_is_dead(url, location) else ("alive", 0)
    return ("unknown", 0)  # 401/403/429/5xx などは一時障害として据え置き


def apply_result(deal: dict, verdict: str, threshold: int, today: str) -> bool:
    """判定結果を deal に反映する。新たに掲載終了が確定したら True を返す。
      alive  : 連続カウンタと掲載終了フラグをクリア（一時誤検知からの復帰）
      dead   : 連続カウンタを進め、閾値到達で delisted_at を確定
      unknown: 据え置き（一時障害では消さない）
    値が無い/0のキーは書かない（deals.json の無駄な差分を避ける）。"""
    if verdict == "alive":
        deal.pop("dead_streak", None)
        deal.pop("delisted_at", None)
        return False
    if verdict == "dead":
        streak = deal.get("dead_streak", 0) + 1
        if streak >= threshold:
            deal.pop("dead_streak", None)  # 確定したのでカウンタは残さない
            if not deal.get("delisted_at"):
                deal["delisted_at"] = today
                return True
            return False
        deal["dead_streak"] = streak
        return False
    return False  # unknown は据え置き
