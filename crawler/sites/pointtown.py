"""ポイントタウン — 新着一覧 /recent（SSR）から取得。"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.pointtown.com/recent"
BASE = "https://www.pointtown.com"


@register
class PointTownAdapter(SiteAdapter):
    key = "pointtown"
    name = "ポイントタウン"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = []
        for item in soup.select("li.l-card__item.c-card")[:max_items]:
            link = item.select_one("a.u-expand-link")
            # UP案件は point-origin（旧値）が併存するためクラス完全一致で現在値を取る
            point = item.select_one("p.c-af-incentive__point")
            if not (link and point):
                continue
            deal_id = link.get("data-item-id")
            if not deal_id:
                m = re.search(r"/item/(\d+)", link.get("href", ""))
                deal_id = m.group(1) if m else None
            if not deal_id:
                continue
            points_text = point.get_text(strip=True)
            if points_text and "%" not in points_text and "％" not in points_text:
                points_text += "pt"  # 一覧は単位なし数値表記のため補う
            href = link.get("href", "")
            condition = item.select_one("p.c-af-incentive__require")
            deal = self.make_deal(
                str(deal_id),
                link.get_text(strip=True),
                points_text,
                href if href.startswith("http") else BASE + href,
                condition.get_text(strip=True) if condition else "",
            )
            deals.append(self.flag_site_new(deal, str(item)))  # UP!バッジ→再新着判定
        return deals
