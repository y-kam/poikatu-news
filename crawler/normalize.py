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
