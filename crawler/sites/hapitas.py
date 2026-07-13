"""ハピタス — sitemap差分方式。

案件一覧のajaxはrobots.txtで禁止されているため、lastmod付きsitemap（毎日01時更新）
から未知IDを検出し、案件詳細ページからタイトル・ポイントを取得する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SitemapDiffAdapter

_ID_RE = re.compile(r"/itemid/(\d+)/")
_TITLE_TAG_RE = re.compile(r"\|\s*([\d.,]+(?:pt|%))還元中")


@register
class HapitasAdapter(SitemapDiffAdapter):
    key = "hapitas"
    name = "ハピタス"
    sitemap_url = "https://hapitas.jp/published-assets/auto-generated/sitemap/sitemap-item.xml.gz"

    def deal_id_from_url(self, url):
        m = _ID_RE.search(url)
        return m.group(1) if m else None

    def fetch_detail(self, fetcher, deal_id, url):
        soup = BeautifulSoup(fetcher.get(url).text, "lxml")
        h1 = soup.select_one("h1.detail_item_label")
        point = soup.select_one(".detail_item_point")
        title = h1.get_text(strip=True) if h1 else ""
        points_text = point.get_text("", strip=True) if point else ""

        # 構造変更に備えた<title>タグからのフォールバック（「案件名 | 126pt還元中 | ハピタス」形式）
        if (not title or not points_text) and soup.title:
            parts = soup.title.get_text().split("|")
            title = title or parts[0].strip()
            m = _TITLE_TAG_RE.search(soup.title.get_text())
            if m and not points_text:
                points_text = m.group(1)

        if not title:
            return None
        return self.make_deal(deal_id, title, points_text, url)
