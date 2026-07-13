"""PONEY — 新着案件一覧（SSR）から取得。"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.poney.jp/service/new_arrival"


@register
class PoneyAdapter(SiteAdapter):
    key = "poney"
    name = "PONEY"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = []
        for item in soup.select("div.resultList")[:max_items]:
            link = item.select_one(".resultDetailsTitle h3 a")
            point = item.select_one("dl.getPoint dd")
            if not (link and point):
                continue
            deal_id = re.search(r"/detail/(\d+)", link.get("href", ""))
            if not deal_id:
                continue
            # ポイントUP時は em が現在値（span側は元値）。%案件はテキストに%を含む
            current = point.select_one("em") or point
            points_text = current.get_text(strip=True)
            if points_text and "%" not in points_text and "％" not in points_text:
                points_text += "pt"
            condition = item.select_one("p.method")
            deal = self.make_deal(
                deal_id.group(1),
                link.get_text(strip=True),
                points_text,
                link["href"],
                condition.get_text(strip=True) if condition else "",
            )
            # ポイントUP！アイコン（point-up.gif）→再新着判定
            deals.append(self.flag_site_new(deal, str(item)))
        return deals
