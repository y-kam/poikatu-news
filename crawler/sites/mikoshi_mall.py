"""MIKOSHIモール（web.mikoshi.jp） — JSON APIのカタログ型。

案件一覧は suggest API が全件（実測569件）を1リクエストで返すJSONのため BeautifulSoup 不要。
新着順ソートが無い全件掲載型なので、初回実行はシード登録して新規追加案件のみを新着として扱う。
還元はポイント固定額（REWARD_TYPE_AMOUNT）と購入額比例（REWARD_TYPE_PERCENT）の2種があり、
%案件は円換算できないため points_text に「%」を残して percent 側へ振る。
"""
import re

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://web.mikoshi.jp"
# 全案件を1リクエストで返す一覧API（uidは空でも全件応答）
SUGGEST_URL = BASE + "/v2/shopping/suggest?uid="

_ID_RE = re.compile(r"/shopping/(\d+)")


@register
class MikoshiMallAdapter(SiteAdapter):
    key = "mikoshi_mall"
    name = "MIKOSHIモール"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        data = fetcher.get(SUGGEST_URL).json()
        deals: dict = {}
        for item in data.get("suggests", []):
            detail_url = item.get("detailUrl") or ""
            m = _ID_RE.search(detail_url)
            name = (item.get("name") or "").strip()
            reward = str(item.get("rewardPoint") or "").strip()
            if not (m and name and reward):
                continue
            deal_id = m.group(1)
            if deal_id in deals:
                continue
            # 還元タイプで単位を補う（%案件は円換算不能なので「%」を残す）
            if item.get("rewardType") == "REWARD_TYPE_PERCENT":
                points_text = reward + "%"
            else:
                points_text = reward + "pt"
            deals[deal_id] = self.make_deal(
                deal_id,
                name,
                points_text,
                f"{BASE}/shopping/{deal_id}",
                (item.get("description") or "").strip(),
            )
        # カタログ型はID差分の完全性が必要なためmax_itemsで切らない（1リクエスト完結）
        return self.apply_seed_policy(list(deals.values()), known)
