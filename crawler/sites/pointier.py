"""ポインティア（Pointier） — 申込系・買い物系の各1ページ目（SSR・EUC-JP）から取得。

一覧は新着（案件ID）降順で、先頭が最新。charset宣言はEUC-JPだがヘッダに無いため
明示デコードする。申込系(cat=1_)と買い物系(cat=2_)を各p=1で取得して統合し、
案件ID降順（＝全体で新着順）に並べて返す新着ポーリング方式（シード不要）。
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://pointier.net/"
# 申込系・買い物系それぞれの一覧1ページ目（新着降順で先頭が最新）
LIST_URLS = (
    "https://pointier.net/?cat=1_&p=1",  # お申し込みでためる
    "https://pointier.net/?cat=2_&p=1",  # お買い物でためる
)
# 詳細ページURL（./open/16740.html / open\16740.html 混在）から案件IDを抽出
_ID_RE = re.compile(r"open/(\d+)\.html")


@register
class PointierAdapter(SiteAdapter):
    key = "pointier"
    name = "ポインティア"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict[str, "object"] = {}
        for url in LIST_URLS:
            resp = fetcher.get(url)
            resp.encoding = "euc-jp"  # ヘッダにcharset宣言が無くmetaはEUC-JP
            soup = BeautifulSoup(resp.text, "lxml")
            for item in soup.select("div#result ul#default_view > li"):
                link = item.select_one("div.title a[href]")
                rate = item.select_one("div.point span.rate")
                if not (link and rate):
                    continue
                href = link.get("href", "").replace("\\", "/")  # \区切り混在を正規化
                m = _ID_RE.search(href)
                if not m or m.group(1) in deals:  # カテゴリ間の重複掲載はIDで排除
                    continue
                deal_id = m.group(1)
                # 獲得条件（申し込み/商品購入/旅行完了 等）はr1内のclass無しdivに入る
                cond_div = item.select_one("div.r1 > div:not([class])")
                deals[deal_id] = self.make_deal(
                    deal_id,
                    link.get_text(strip=True),
                    rate.get_text(strip=True),  # "800Ｐ" / "5.5％"（%はparse_pointsが判別）
                    urljoin(BASE, href),
                    cond_div.get_text(strip=True) if cond_div else "",
                )
        # 案件IDは発番順のため降順＝全体で新着順。統合後に並べ直す
        ordered = sorted(deals.values(), key=lambda d: int(d.deal_id), reverse=True)
        return ordered[:max_items]