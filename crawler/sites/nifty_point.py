"""ニフティポイントクラブ — 新着順の案件検索一覧（SSR）から取得。"""
from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://lifemedia.jp/service/asearch/all?aorder=1"
BASE = "https://lifemedia.jp"
MAX_PAGES = 80  # 20件/頁。安全側に余裕を持たせる


@register
class NiftyPointAdapter(SiteAdapter):
    key = "nifty_point"
    name = "ニフティポイントクラブ"

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
        for item in soup.select("li.stack__item"):
            link = item.select_one("a.stack__item__inner")
            title = item.select_one("p.item__unit.mod-name em")
            point = item.select_one("span.f-point")
            if not (link and title and point):
                continue
            href = link.get("href", "")
            deal_id = href.rstrip("/").rsplit("/", 1)[-1]  # /shopping/detail/{id} の末尾
            if not deal_id:
                continue
            condition = item.select_one("p.item__unit.mod-sub")
            deal = self.make_deal(
                deal_id,
                title.get_text(strip=True),
                point.get_text("", strip=True),
                href if href.startswith("http") else BASE + href,
                condition.get_text(strip=True) if condition else "",
            )
            # campicons の UP/NEW アイコン（ico_up.png / ico_new.png）→再新着判定
            deals.append(self.flag_site_new(deal, str(item)))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
