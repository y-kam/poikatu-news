"""バリューポイントクラブ — 新着ショップ一覧（SSR）から取得。"""
from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.value-point.jp/search/merchant/list/1/?disp=1&sort=1"
BASE = "https://www.value-point.jp"
MAX_PAGES = 60  # 32件/頁。安全側に余裕を持たせる


@register
class ValuePointAdapter(SiteAdapter):
    key = "value_point"
    name = "バリューポイントクラブ"

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
        for item in soup.select("div#results div.list"):
            deal_id = item.select_one("span.adprofileIdHidden")
            link = item.select_one("p.ttl a")
            point = item.select_one("p.ico-point")
            if not (deal_id and link and point):
                continue
            href = link.get("href", "")
            deals.append(self.make_deal(
                deal_id.get_text(strip=True),
                link.get_text(strip=True),
                point.get_text("", strip=True),
                href if href.startswith("http") else BASE + href,
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
