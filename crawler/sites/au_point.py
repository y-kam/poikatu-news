"""auポイントプログラム — reward/free 新着タブ（SSR・UTF-8）から取得。

モバイルUAが必須。PC UAだとアプリ/QR誘導ページが返り案件一覧が得られないため、
iPhone Safari相当のUAを持つ専用フェッチャを生成する。
新着タブ1頁（実測約28件）を取得し、先頭が新着なのでそのまま返す（シード不要）。
還元は固定ポイント（1P=1円）で、会員種別のうち一般値（tmr-txt--list-general）を採用する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter
from crawler.fetch import PoliteFetcher, MOBILE_UA  # PC UAだとQR/アプリ誘導になるためモバイルUAを使う

LIST_URL = "https://enjoy.point.auone.jp/reward/free"

# 詳細URL（例: /reward/detail?type=8&campaignId=1401）から type と campaignId を抽出
_TYPE_RE = re.compile(r"[?&]type=([^&]+)")
_CID_RE = re.compile(r"[?&]campaignId=([^&]+)")


@register
class AuPointAdapter(SiteAdapter):
    key = "au_point"
    name = "auポイントプログラム"

    def make_fetcher(self, interval: float | None = None) -> PoliteFetcher:
        # モバイルUA必須のため専用フェッチャを生成
        return PoliteFetcher(
            interval=interval or self.request_interval,
            user_agent=MOBILE_UA,
        )

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        soup = BeautifulSoup(fetcher.get(LIST_URL).text, "lxml")
        deals = []
        for item in soup.select("li.r-stack_item")[:max_items]:
            link = item.select_one("a.r-stack_item__link")
            title = item.select_one(".r-stack_item__head span")
            # 会員種別のうち一般値を採用（tmr-txt--list-pp はプレミアム値なので使わない）
            point = item.select_one("dd.tmr-txt--list-general")
            if not (link and title and point):
                continue
            href = link.get("href", "")
            m_type = _TYPE_RE.search(href)
            m_cid = _CID_RE.search(href)
            if not (m_type and m_cid):
                continue
            deal_id = f"{m_type.group(1)}:{m_cid.group(1)}"  # type+campaignIdで一意化
            deal = self.make_deal(
                deal_id,
                title.get_text(strip=True),
                point.get_text(strip=True),  # 例 "70P"（1P=1円）
                href,  # 一覧の href は絶対URL
            )
            # NEWラベル（r-stack_item__label）→再新着判定
            deals.append(self.flag_site_new(deal, str(item)))
        return deals