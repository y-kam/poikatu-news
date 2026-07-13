"""ワラウ — 全カテゴリ・新着順一覧（SSR）から取得。"""
from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.warau.jp/contents/point/category?point_group=0&sort=new"
BASE = "https://www.warau.jp"


@register
class WarauAdapter(SiteAdapter):
    key = "warau"
    name = "ワラウ"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = {}
        for item in soup.select("li.pointList-ptItem"):
            link = item.select_one("a.pointList-ptItem_Link")
            title = item.select_one("span.pointList-ptItem_Name")
            number = item.select_one("span.pointList-ptItem_PtInfo-point")
            unit = item.select_one("span.pointList-ptItem_PtInfo-unit")
            if not (link and title and number):
                continue
            deal_id = link.get("data-offerwall", "").strip()
            if not deal_id or deal_id in deals:  # 一覧内の重複掲載をIDで排除
                continue
            points_text = number.get_text(strip=True) + (unit.get_text(strip=True) if unit else "")
            href = link.get("href", "")
            deals[deal_id] = self.make_deal(
                deal_id,
                title.get_text(strip=True),
                points_text,
                href if href.startswith("http") else BASE + href,
            )
        return list(deals.values())[:max_items]
