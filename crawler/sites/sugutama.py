"""すぐたま — 掲載開始日降順の案件一覧（SSR）から取得。単位はmile（2mile=1円）。"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.netmile.co.jp/sugutama/ads/list?q%5Bs%5D=start_date+desc"
BASE = "https://www.netmile.co.jp"


@register
class SugutamaAdapter(SiteAdapter):
    key = "sugutama"
    name = "すぐたま"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = []
        for item in soup.select("div.main_detail.searchlist")[:max_items]:
            link = item.select_one("a.area[href]")
            title = item.select_one("div.cp_title")
            mile = item.select_one("div.cp_mile")
            if not (link and title and mile):
                continue
            deal_id = re.search(r"/sugutama/ads/(\d+)", link["href"])
            if not deal_id:
                continue
            for base_price in mile.select("span.base"):
                base_price.decompose()  # 改定前の旧表記を除去
            number = mile.select_one("span.main_p")
            if not number:
                continue
            value = number.get_text(strip=True)
            # %案件かどうかはブロック全体の%有無で判定（mile案件は単位表記が省かれている）
            is_percent = "%" in mile.get_text() or "％" in mile.get_text() or "%" in value
            points_text = value if "%" in value else value + ("%" if is_percent else "mile")
            deals.append(self.make_deal(
                deal_id.group(1),
                title.get_text(strip=True),
                points_text,
                BASE + link["href"].split("?")[0],
            ))
        return deals
