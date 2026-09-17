"""フルーツメール — 新着案件一覧（SSR）から取得。

日次は新着専用一覧 /point/list/new_point の1ページ目（新着ポーリング型）。
全件バックフィルは全案件一覧 /point/list/all（100件/頁・`?page=N`。上位の注目案件が
各ページ先頭に重複して載るため、base.backfill_deals の実行内重複排除で吸収する。
2026-09-17実測: 4頁で433件超）を page_url で巡回する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.fruitmail.net/point/list/new_point"
ALL_URL = "https://www.fruitmail.net/point/list/all"  # 全案件一覧（バックフィル用）
BASE = "https://www.fruitmail.net"
MAX_PAGES = 60  # 100件/頁。全ページ巡回時の暴走防止上限


@register
class FruitmailAdapter(SiteAdapter):
    key = "fruitmail"
    name = "フルーツメール"

    def page_url(self, page):
        # バックフィルは全案件一覧を巡回する（1頁目は ?page 無し、2頁目以降は ?page=N）
        if page > MAX_PAGES:
            return None
        if page == 1:
            return ALL_URL
        return f"{ALL_URL}?page={page}"

    def parse_list(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("li.point_categoryItem"):
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

    def fetch_deals(self, known, max_items):
        # 日次は新着専用一覧の1頁目のみ（新着ポーリング型）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(LIST_URL))[:max_items]
