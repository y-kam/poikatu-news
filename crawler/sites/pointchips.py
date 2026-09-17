"""PointChips（ポイントチップス） — 新着順の全案件一覧 /new（SSR）から取得。

2025年開始のポイントサイト（フリービット系 株式会社LinkAd）。`/new` は全案件（2026-09-17時点で
約730件・37頁）が新着順に並ぶ一覧で、`?page=N`（20件/頁）でページングできる。
日次は先頭 DAILY_PAGES 頁の新着ポーリング、全件バックフィルは base.backfill_deals が
page_url で全ページを巡回する。カード（a[data-testid=promotion-card]）の img alt が案件名、
[data-testid=promotion-point-area] がポイント表記（"9,855 pt" / "1%"）。獲得条件は一覧に無い。
掲載終了は 404 を返すため汎用HTTP判定で検知できる。robots.txt は全UA Allow（/api/ 等のみ Disallow）。
1pt=1円（rate=1.0。交換レートの明記をサイト上で確認できず、他社と同一案件の還元額
（PayPayカード 2,550pt・U-NEXT無料トライアル 1,500pt 等）から推定。要確認）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://pointchips.com"
LIST_URL = BASE + "/new"  # 全案件の新着順一覧（20件/頁）
DAILY_PAGES = 2           # 日次で見る先頭ページ数（40件）
MAX_PAGES = 100           # 全ページ巡回時の暴走防止上限（2026-09-17時点で37頁）
_ID_RE = re.compile(r"/promotion/detail/(\d+)")


@register
class PointChipsAdapter(SiteAdapter):
    key = "pointchips"
    name = "PointChips"

    def page_url(self, page):
        # 1頁目は LIST_URL そのまま、2頁目以降は ?page=N（1始まり）
        if page > MAX_PAGES:
            return None
        if page == 1:
            return LIST_URL
        return f"{LIST_URL}?page={page}"

    def parse_list(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for card in soup.select('a[data-testid="promotion-card"][href]'):
            m = _ID_RE.search(card["href"])
            img = card.select_one("img[alt]")
            point = card.select_one('[data-testid="promotion-point-area"]')
            if not (m and point):
                continue
            title = img["alt"].strip() if img else card.get_text(" ", strip=True)
            if not title:
                continue
            deals.append(self.make_deal(
                m.group(1),
                title,
                point.get_text(" ", strip=True),  # 例 "9,855 pt" / "1%"
                BASE + card["href"],
            ))
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        for page in range(1, DAILY_PAGES + 1):
            for deal in self.parse_list(fetcher.get(self.page_url(page))):
                deals.setdefault(deal.deal_id, deal)
        return list(deals.values())[:max_items]
