"""クラシルリワード（web版） — 広告案件一覧 /ads（SSR・UTF-8）から取得。

一覧は案件ID降順＝掲載新着順で並ぶため、新着順1頁ポーリング型として扱う
（先頭に新着が来るのでシード不要）。1頁20件・案件IDが降順に並ぶ。

ポイント単位は「コイン」。100コイン=1円のため rate=0.01（config/sites.json）。
還元率アップ中の案件は現在値の左に旧値が `div.line-through` で併記されるため、
現在値だけを表す `span.text-rs-red-main...text-base` を構造ベースで拾う。
Tailwindのユーティリティクラスは変化しやすいので、抽出はDOM構造を優先する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://www.rewards.kurashiru.com"
LIST_URL = BASE + "/ads?page={}"
MAX_PAGES = 120  # 1頁20件。全ページ巡回時の暴走防止上限

_ID_RE = re.compile(r"/ads/(\d+)")


@register
class KurashiruRewardAdapter(SiteAdapter):
    key = "kurashiru_reward"
    name = "クラシルリワード"

    def page_url(self, page):
        # page=1 が現行dailyの先頭ページ（?page=1）と一致。以降はpageを差し替え。
        if page > MAX_PAGES:
            return None
        return LIST_URL.format(page)

    def parse_list(self, resp):
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        seen = set()  # 頁内の重複ID排除
        for a in soup.select('a[href^="/ads/"]'):
            deal = self._parse_card(a)
            if not deal or deal.deal_id in seen:
                continue
            seen.add(deal.deal_id)
            deals.append(deal)
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals = []
        seen = set()  # 頁跨ぎの重複ID排除
        for page in range(1, MAX_PAGES + 1):
            cards = self.parse_list(fetcher.get(self.page_url(page)))
            if not cards:
                break  # 案件が無い＝最終頁を越えた
            for deal in cards:
                if deal.deal_id in seen:
                    continue
                seen.add(deal.deal_id)
                deals.append(deal)
                if len(deals) >= max_items:
                    return deals
        return deals

    def _parse_card(self, a):
        """一覧カード（a要素）1件から Deal を生成する。抽出不能ならNone"""
        m = _ID_RE.search(a.get("href", ""))
        if not m:
            return None
        deal_id = m.group(1)

        title_el = a.select_one("h3")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # 還元率アップ中は旧値の div.line-through が併記されるので除去し、
        # 現在値を表す text-base の赤字span（構造上ここが現在値）を取る。
        for old in a.select("div.line-through"):
            old.decompose()
        point_el = a.select_one("span.text-rs-red-main.font-bold.text-base")
        points_text = point_el.get_text(strip=True) if point_el else ""
        # %案件はそのまま（parse_pointsがpercent判定）。コイン案件は単位を補って
        # 数値×rate=円換算させる（例 "24,000" → "24,000コイン"）。
        if points_text and "%" not in points_text and "％" not in points_text:
            points_text += "コイン"

        # 獲得条件（「〜 で」の一文）。カード内先頭のテキストdivから拾う。
        condition = ""
        cond_el = a.select_one("div.text-sm > div")
        if cond_el:
            condition = cond_el.get_text(" ", strip=True)

        return self.make_deal(
            deal_id, title, points_text, BASE + a.get("href"), condition,
        )
