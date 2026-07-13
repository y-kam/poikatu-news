"""ポイントミュージアム — sitemap差分方式（Shift_JIS詳細ページ）。

新着順の一覧ページが無いためカタログ型のsitemap（sitemap-list.xml）から未知IDを
検出し、案件詳細ページ ct.asp?adv={id} からタイトル・ポイントを取得する。

sitemapには ct.asp?adv= と s_ct.asp?adv= の両方が載るが同一IDが重複するため、
基準URLの ct.asp?adv= のみをIDとして採用する（約1,576件）。lastmodは大半が空で
順序情報として使えないため未知IDを順に max_detail_fetch 件だけ詳細取得する。
sitemapには既に配信終了した案件（詳細が「インフォメーション」ページになる）も
多数含まれるため、タイトル/ポイントが取れないページはNoneで捨てる。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SitemapDiffAdapter

# 基準の詳細URL（ct.asp）のみを対象IDとする。s_ct.asp は先頭の "/" で除外される
_ID_RE = re.compile(r"/ct\.asp\?adv=([A-Za-z0-9]+)")


@register
class PointMuseumAdapter(SitemapDiffAdapter):
    key = "point_museum"
    name = "ポイントミュージアム"
    sitemap_url = "https://www.point-museum.com/sitemap-list.xml"

    def deal_id_from_url(self, url):
        """sitemap内のURLから案件ID（英数字）を抽出。ct.asp以外はNone"""
        m = _ID_RE.search(url)
        return m.group(1) if m else None

    def fetch_detail(self, fetcher, deal_id, url):
        """詳細ページ（Shift_JIS）からタイトル・ポイントを取得する。

        配信終了案件は共通の「インフォメーション」ページになり h2/.bt_pt が無いため
        Noneを返して除外する。ポイント表記は "68,000pt" や "80pt (100円購入毎に)" の
        ように単位付きの単一値（旧値併記なし）でそのまま渡してよい（10pt=1円）。
        """
        resp = fetcher.get(url)
        resp.encoding = "cp932"  # charset宣言が無いためShift_JISを明示
        soup = BeautifulSoup(resp.text, "lxml")
        h2 = soup.select_one("#pointdetaile h2")
        point = soup.select_one(".bt_pt")
        if not (h2 and point):
            return None  # 配信終了・存在しない案件
        title = h2.get_text(strip=True)
        points_text = point.get_text(" ", strip=True)
        if not (title and points_text):
            return None
        return self.make_deal(deal_id, title, points_text, url)
