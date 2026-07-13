"""複数サイトに掲載されている同一案件の名寄せ（グルーピング）。

1. 正規化タイトルの完全一致でグループ化
2. 高しきい値のあいまい一致（difflib）で近接グループを統合
   （条件違いの別案件を誤って統合しないよう保守的なしきい値にする）
"""
from difflib import SequenceMatcher

from crawler.normalize import normalize_title

FUZZY_THRESHOLD = 0.92
FUZZY_MAX_KEYS = 1500  # これを超える正規化タイトル数ではあいまい一致を打ち切る（O(n^2)回避）


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_THRESHOLD


def group_deals(deals: list[dict]) -> list[dict]:
    """案件リストを名寄せし、グループのリストを返す。

    各グループ: {"title": 代表タイトル, "deals": [案件...], "sites": サイト数,
                 "best_yen": グループ内最高円換算額, "best_percent": 最高%還元}
    """
    buckets: dict[str, list[dict]] = {}
    for deal in deals:
        key = normalize_title(deal["title"])
        buckets.setdefault(key, []).append(deal)

    # あいまい一致による統合（キー同士を比較し、類似キーを先勝ちでマージ）。
    # difflibの総当たりはキー数に対してO(n^2)のため、全件バックフィルで数千件規模に
    # なると実用外に遅くなる。閾値超過時は完全一致グループ化のみに留める（実害は
    # 「表記ゆれのある同一案件が別グループになる」程度で、比較機能は完全一致で成立する）。
    keys = sorted(buckets, key=lambda k: -len(buckets[k]))
    if len(keys) <= FUZZY_MAX_KEYS:
        merged_keys: dict[str, str] = {}
        for i, key in enumerate(keys):
            if key in merged_keys:
                continue
            for other in keys[i + 1:]:
                if other in merged_keys:
                    continue
                if _similar(key, other):
                    merged_keys[other] = key
        for src, dst in merged_keys.items():
            buckets[dst].extend(buckets.pop(src))

    groups = []
    for key, items in buckets.items():
        if not key:
            continue
        # 同一サイト内の重複（同じ案件IDの再掲）はまとめない — 別条件の可能性があるため残す
        items.sort(key=lambda d: (d.get("yen") or 0, d.get("percent") or 0), reverse=True)
        best = items[0]
        groups.append({
            "title": best["title"],  # 最高還元の案件のタイトルを代表にする
            "deals": items,
            "sites": len({d["site"] for d in items}),
            "best_yen": max((d.get("yen") or 0 for d in items), default=0) or None,
            "best_percent": max((d.get("percent") or 0 for d in items), default=0) or None,
        })
    # 複数サイト掲載を優先し、還元額が大きい順に並べる
    groups.sort(key=lambda g: (g["sites"], g["best_yen"] or 0, g["best_percent"] or 0), reverse=True)
    return groups
