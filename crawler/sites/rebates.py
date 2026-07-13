"""Rebates（楽天リーベイツ） — ストア型カタログ（%ポイントバック）。

/stores ページに全ストアのJSON（window.INITIAL_FETCH_STATE）が埋め込まれている。
全店掲載型のため、初回実行はシード登録し新規追加ストアのみを新着として扱う。
"""
import json
import re

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.rebates.jp/stores"
BASE = "https://www.rebates.jp/"

_STATE_RE = re.compile(r"window\.INITIAL_FETCH_STATE\s*=\s*(\{.*?\})\s*</script>", re.DOTALL)


def _find_stores(state: dict) -> list[dict]:
    """トップレベルキーがビルド依存ハッシュのため、data.stores を持つエントリを探す"""
    for value in state.values():
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict) and isinstance(data.get("stores"), list):
                return data["stores"]
    return []


@register
class RebatesAdapter(SiteAdapter):
    key = "rebates"
    name = "Rebates"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        html = fetcher.get(LIST_URL).text
        m = _STATE_RE.search(html)
        if not m:
            raise RuntimeError("INITIAL_FETCH_STATE が見つからない（ページ構造変更の可能性）")
        state = json.loads(m.group(1).replace(":undefined", ":null"))
        deals = []
        for store in _find_stores(state):
            store_id = store.get("id")
            name = (store.get("name") or "").strip()
            if not (store_id and name) or store.get("status") not in (None, "active"):
                continue
            deals.append(self.make_deal(
                str(store_id),
                name,
                (store.get("rewardText") or "").strip(),
                BASE + (store.get("link") or "").lstrip("/"),
            ))
        # カタログ型はID差分の完全性が必要なためmax_itemsで切らない（1ページ完結）
        return self.apply_seed_policy(deals, known)
