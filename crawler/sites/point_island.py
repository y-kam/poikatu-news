"""ポイントアイランド — 新着順一覧（plist.asp・Shift_JIS/SSR）から取得。

株式会社システムエッジ運営。ASP製サイトで文字コードはShift_JIS（cp932）。
新着順（qt=1）1ページ40件が先頭に新着が来るポーリング型のため、1ページのみ取得する。
1案件は幅260のネストした<table>で、タイトルは a.aview>strong、案件IDは
onclick="advview('ID')"（数字/英字接頭辞が混在）、ポイントは先頭の span.font1 内の
「N ポイント」表記。10ポイント=1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

# 新着順（qt=1）・全カテゴリ（cat=0000）1ページ目。40件/ページ。
LIST_URL = "https://www.point-island.com/plist.asp?qt=1&cat=0000&st=000&p=1"
BASE = "https://www.point-island.com"
MAX_PAGES = 45  # 全39頁1,526件・40件/頁。安全側に余裕を持たせる

# onclick="advview('1903020')" / advview('mHIM99') から案件IDを取り出す
_ID_RE = re.compile(r"advview\('([^']+)'\)")
# ポイントセル内の「130000ポイント」「16ポイント」から現在値だけを取り出す
_POINT_RE = re.compile(r"[\d,]+\s*ポイント")


@register
class PointIslandAdapter(SiteAdapter):
    key = "point_island"
    name = "ポイントアイランド"

    def page_url(self, page):
        # LIST_URL の p=1 を page に差し替える。page==1 は LIST_URL と完全一致。
        if page > MAX_PAGES:
            return None
        return LIST_URL.replace("&p=1", f"&p={page}")

    def parse_list(self, resp):
        resp.encoding = "cp932"  # charset宣言が無くShift_JISのため明示
        soup = BeautifulSoup(resp.text, "lxml")

        deals = []
        for link in soup.select("a.aview"):
            m = _ID_RE.search(link.get("onclick", ""))
            if not m:
                continue
            deal_id = m.group(1)

            strong = link.select_one("strong")
            title = strong.get_text(strip=True) if strong else link.get_text(strip=True)
            if not title:
                continue

            # 各案件は幅260のネストtable。先頭の span.font1 がポイント+条件セル
            item = link.find_parent("table")
            spans = item.select("span.font1") if item else []
            cell = spans[0].get_text("\n", strip=True) if spans else ""

            # ポイントは「N ポイント」だけを渡す（「100円購入ごとに16ポイント」等の
            # 前置き数値や、説明文中の「%」表記に引っ張られないようにするため）。
            pm = _POINT_RE.search(cell)
            if not pm:
                continue
            points_text = pm.group(0)

            # 条件はポイント行以外の行（例: カード発行 / 口座開設後取引 / 買い物）
            condition = ""
            for line in cell.split("\n"):
                line = line.strip()
                if line and not _POINT_RE.search(line):
                    condition = line
                    break

            deals.append(self.make_deal(
                deal_id,
                title,
                points_text,
                f"{BASE}/ct.asp?adv={deal_id}",  # 案件詳細（クリック計測）URL
                condition,
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
