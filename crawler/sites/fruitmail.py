"""フルーツメール — 新着案件一覧（SSR）から取得。"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.fruitmail.net/point/list/new_point"
BASE = "https://www.fruitmail.net"


@register
class FruitmailAdapter(SiteAdapter):
    key = "fruitmail"
    name = "フルーツメール"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = []
        for item in soup.select("li.point_categoryItem")[:max_items]:
            link = item.select_one("a.point_categoryItem__link")
            title = item.select_one(".point_categoryItem__title")
            value = item.select_one(".point_value")
            if not (link and title and value):
                continue
            deal_id = re.search(r"ksid=(\d+)", link.get("href", ""))
            if not deal_id:
                continue
            for old_price in value.select("del"):
                old_price.decompose()  # ポイントUP時の元値を除去し現在値のみ残す
            condition = item.select_one(".point_categoryItem__caption")
            deal = self.make_deal(
                deal_id.group(1),
                title.get_text(strip=True),
                value.get_text("", strip=True),
                BASE + link["href"],
                condition.get_text(strip=True) if condition else "",
            )
            # 増額中の現在値div（point_value__up。delの旧値と併存）→再新着判定
            deals.append(self.flag_site_new(deal, str(item)))
        return deals
