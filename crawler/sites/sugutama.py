"""すぐたま — 掲載開始日降順の案件一覧（SSR）から取得。単位はmile（2mile=1円）。

2ページ目以降は `&page=N`（20件/頁。2026-09-09/09-17実測）。日次は1ページ目のみの
新着ポーリング、全件バックフィルは base.backfill_deals が page_url で全ページを巡回する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.netmile.co.jp/sugutama/ads/list?q%5Bs%5D=start_date+desc"
BASE = "https://www.netmile.co.jp"
MAX_PAGES = 150  # 20件/頁。2026-09-17時点で40頁超あり。全ページ巡回時の暴走防止上限


@register
class SugutamaAdapter(SiteAdapter):
    key = "sugutama"
    name = "すぐたま"

    def page_url(self, page):
        # 1頁目は現行の新着順LIST_URLそのまま、2頁目以降は &page=N（1始まり）。
        if page > MAX_PAGES:
            return None
        if page == 1:
            return LIST_URL
        return f"{LIST_URL}&page={page}"

    def parse_list(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div.main_detail.searchlist"):
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

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（新着ポーリング型）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(LIST_URL))[:max_items]
