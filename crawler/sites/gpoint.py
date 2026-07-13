"""Gポイント — ショップ検索一覧（SSR・Shift_JIS）の全ページ巡回＋ID差分。

新着順ソートが存在しないため全件を取得して差分検知する（カタログ型・初回はシード登録）。
対象サイト中もっともリクエスト数が多いため間隔は空けつつページ上限で抑制する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://pmall.gpoint.co.jp/shopsearch/?page={}"
MAX_PAGES = 120  # 実測30件/ページ・全1,000店超を見込んだ上限（暴走防止）


@register
class GpointAdapter(SiteAdapter):
    key = "gpoint"
    name = "Gポイント"
    request_interval = 5.0  # ページ数が多いため間隔を短縮（それでも全体で数分）

    def page_url(self, page):
        # page=1 が現行dailyの先頭ページ（?page=1）と一致。以降はpageを差し替え。
        if page > MAX_PAGES:
            return None
        return LIST_URL.format(page)

    def parse_list(self, resp):
        resp.encoding = "cp932"  # shopsearchはShift_JIS
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div.catshop-box"):
            link = item.select_one("div.name > a")
            point = item.select_one("div.point > span")
            if not (link and point):
                continue
            deal_id = re.search(r"/allshop/(\d+)/", link.get("href", ""))
            if not deal_id:
                continue
            deals.append(self.make_deal(
                deal_id.group(1),
                link.get_text(strip=True),
                point.get_text(strip=True),
                link["href"],
            ))
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        for page in range(1, MAX_PAGES + 1):
            items = self.parse_list(fetcher.get(self.page_url(page)))
            if not items:
                break
            before = len(deals)
            for deal in items:
                deals.setdefault(deal.deal_id, deal)  # 頁跨ぎの重複IDは初出優先
            if len(deals) == before:  # 全ページ既出（最終ページ以降のループ）なら打ち切り
                break
        # カタログ型はID差分の完全性が必要なためmax_itemsで切らない
        return self.apply_seed_policy(list(deals.values()), known)
