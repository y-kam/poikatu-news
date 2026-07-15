"""案件名の正規化とポイント表記のパース。

正規化した案件名は複数サイト間の名寄せ（同一案件のグルーピング）キーに使う。
"""
import re
import unicodedata

# 名寄せの邪魔になる定型注記（条件は各サイトの元表記側に残るので削ってよい）
_BRACKET_RE = re.compile(r"[【\[（(].*?[】\]）)]")
_NOISE_RE = re.compile(r"[\s　・、。．,\.!！?？~〜\-ー―_/／|｜:：;；'\"「」『』☆★◆■●○†※+＋*＊]+")
_SUFFIX_WORDS = (
    "新規", "初回", "限定", "公式", "アプリ", "サイト",
)


def normalize_title(title: str) -> str:
    """案件名を名寄せ用キーに変換する（NFKC・小文字化・注記/記号除去）"""
    t = unicodedata.normalize("NFKC", title).lower()
    t = _BRACKET_RE.sub("", t)
    t = _NOISE_RE.sub("", t)
    return t


# ポイント表記の表示用正規化に使う。数値トークン（既存カンマ・小数含む）を1つずつ拾って
# 桁区切りを付け直す。先頭の矢印類（"→ 2,500pt" のようなノイズ）は表示前に落とす。
_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LEAD_NOISE_RE = re.compile(r"^[\s→⇒⟶≫➡»›>]+")


def _regroup_number(m: re.Match) -> str:
    """数値トークンを桁区切り付きに整形する（小数部は保持。整形不能ならそのまま）。"""
    token = m.group(0)
    int_part, _, frac = token.replace(",", "").partition(".")
    try:
        grouped = f"{int(int_part):,}"
    except ValueError:
        return token
    return grouped + ("." + frac if frac else "")


def normalize_points_text(text: str) -> str:
    """ポイント表記を表示用に整える（値は変えず見た目だけ揃える）。
    NFKC（全角記号・NBSPの正規化。例: ％→% / ％Ｇ→%G）→ 連続空白の圧縮 → 先頭の矢印
    ノイズ除去 → 数値へ桁区切り付与（例: 10000pt→10,000pt）。単位名（pt/P/コイン/マイル等）は
    各サイトの通貨表記なので改名しない。円換算(yen)・%(percent)は別途保存済みで本処理は非影響。"""
    if not text:
        return text
    t = unicodedata.normalize("NFKC", text)
    t = " ".join(t.split())  # NBSP由来を含む連続空白を1つに畳む
    stripped = _LEAD_NOISE_RE.sub("", t)
    t = stripped or t  # 矢印のみ等で空になる場合は元を残す
    return _NUM_TOKEN_RE.sub(_regroup_number, t)


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_POINT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:pt|ポイント|p|マイル|G)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"([\d,]+(?:\.\d+)?)")


def parse_points(points_text: str, rate: float) -> tuple[float | None, float | None]:
    """ポイント表記文字列から (円換算額, %還元率) を求める。判別不能なら (None, None)。

    「50%」のような購入額比例の案件は円換算できないため percent 側に入れる。
    """
    text = unicodedata.normalize("NFKC", points_text)
    m = _PERCENT_RE.search(text)
    if m:
        return None, float(m.group(1))
    m = _POINT_RE.search(text) or _NUMBER_RE.search(text)
    if m:
        points = float(m.group(1).replace(",", ""))
        return round(points * rate, 2), None
    return None, None
