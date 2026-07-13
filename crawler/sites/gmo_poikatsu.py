"""GMOポイ活（colleee.net） — 新着順の広告一覧（SSR）から取得。"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://colleee.net/programs/list/3"
MAX_PAGES = 200  # 10件/頁。全ページ巡回時の暴走防止上限


@register
class GmoPoikatsuAdapter(SiteAdapter):
    key = "gmo_poikatsu"
    name = "GMOポイ活"

    def page_url(self, page):
        # page=1 は現行dailyの先頭URL（/programs/list/3）と完全一致。
        # 2頁目以降はパス形式 /programs/list/3/{page}。
        if page > MAX_PAGES:
            return None
        if page == 1:
            return LIST_URL
        return f"{LIST_URL}/{page}"

    def parse_list(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for link in soup.select("a.programs_list__detail"):
            title = link.select_one("p.programs_list__detail__txt")
            point = link.select_one("p.programs_list__detail__point")
            deal_id = re.search(r"/programs/(\d+)", link.get("href", ""))
            if not (title and point and deal_id):
                continue
            condition = link.select_one("dl.programs_list__detail__chart dd")
            deals.append(self.make_deal(
                deal_id.group(1),
                title.get_text(strip=True),
                point.get_text(" ", strip=True),
                link["href"],
                condition.get_text(strip=True) if condition else "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
