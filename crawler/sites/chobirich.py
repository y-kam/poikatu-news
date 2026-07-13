"""ちょびリッチ — サービス系・ショッピング系の新着一覧（SSR）＋ スマホ「アプリで貯める」。

アプリ・ゲームのインストール案件はPCの新着一覧には出ず、スマホ版の「アプリで貯める」
(/smartphone/) にのみ並ぶ。/smartphone/ はPC UAだと空のシェルを返すため、モバイルUAで
取得する（項目構造もPC一覧とは別）。deal_id は /ad_details/NN で共通のため重複排除される。
"""
import re

from bs4 import BeautifulSoup

from crawler.fetch import PoliteFetcher, MOBILE_UA
from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URLS = (
    "https://www.chobirich.com/earn/new_list",
    "https://www.chobirich.com/shopping/new_list/",
)
APP_URL = "https://www.chobirich.com/smartphone/"  # アプリで貯める（モバイルUA必須）
BASE = "https://www.chobirich.com"
# 一覧リンクの案件ID。通常は /ad_details/NN、ステップアップ案件は /ad_details/redirect/NN。
_ID_RE = re.compile(r"/ad_details/(?:redirect/)?(\d+)")


@register
class ChobirichAdapter(SiteAdapter):
    key = "chobirich"
    name = "ちょびリッチ"

    # 403 Forbidden 対策: トップからの遷移に見せる。Referer を伴うため
    # Sec-Fetch-Site を同一オリジン遷移（same-origin）に整合させる。
    extra_headers = {
        "Referer": BASE + "/",
        "Sec-Fetch-Site": "same-origin",
    }
    # 403 が断続的に発生するため、間隔を空けて最大2回まで再試行する（リストURLは軽い）
    max_retries = 2

    def _mobile_fetcher(self) -> PoliteFetcher:
        # スマホ「アプリで貯める」はモバイルUAが必須（PC UAだと空シェルが返る）
        return PoliteFetcher(
            interval=self.request_interval, user_agent=MOBILE_UA,
            headers=self.extra_headers, max_retries=self.max_retries)

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals = {}
        for list_url in LIST_URLS:  # PC新着（サービス系・ショッピング系）
            soup = BeautifulSoup(fetcher.get(list_url).text, "lxml")
            for item in soup.select("li.ad-category__ad"):
                link = item.select_one("a[href*='/ad_details/']")
                title = item.select_one("h4.ad-category__ad__name--text")
                point = item.select_one("div.ad-category__ad__pt")
                if not (link and title and point):
                    continue
                m = _ID_RE.search(link.get("href", ""))
                if not m or m.group(1) in deals:
                    continue
                deal = self.make_deal(
                    m.group(1),
                    title.get_text(strip=True),
                    point.get_text("", strip=True),
                    BASE + link["href"],
                )
                # 「新規掲載広告」アイコン（ad-category__ad__new-icon）→再新着判定
                deals[m.group(1)] = self.flag_site_new(deal, str(item))

        # スマホ「アプリで貯める」（アプリ・ゲームDL案件。PC一覧には出ない）
        soup = BeautifulSoup(self._mobile_fetcher().get(APP_URL).text, "lxml")
        for item in soup.select("li.CommonSearchItem"):
            link = item.select_one("a.CommonSearchItem__inner")
            title = item.select_one("h2.CommonSearchItem__itemName")
            point = item.select_one("p.CommonSearchItem__itemPt")
            if not (link and title and point):
                continue
            m = _ID_RE.search(link.get("href", ""))
            if not m or m.group(1) in deals:
                continue
            condition = item.select_one("p.CommonSearchItem__itemDescription")
            deals[m.group(1)] = self.make_deal(
                m.group(1),
                title.get_text(strip=True),
                point.get_text("", strip=True),
                f"{BASE}/ad_details/{m.group(1)}/",  # 詳細の正規URL
                condition.get_text(strip=True) if condition else "",
            )
        return list(deals.values())[:max_items]
