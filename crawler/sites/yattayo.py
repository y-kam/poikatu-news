"""やったよ.ねっと — 新着順一覧（SSR・HTTP・UTF-8）から取得。

http://pint.yattayo.net/?UACT=cmpnL&USRT=0 が新着順（USRT=0）の1ページ目。
新着が先頭に来るポーリング型のためシード登録は不要で、1ページだけ取得して返す。
サーバはHTTPS未対応・charset宣言も無いため、UTF-8を明示してからパースする。
10pt = 1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "http://pint.yattayo.net/?UACT=cmpnL&USRT=0"
BASE = "http://pint.yattayo.net/"

_UID_RE = re.compile(r"UID1=(\d+)")


@register
class YattayoAdapter(SiteAdapter):
    key = "yattayo"
    name = "やったよ.ねっと"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        resp = fetcher.get(LIST_URL)
        resp.encoding = "utf-8"  # charset宣言が無いためUTF-8を明示
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        # campaignlist1/2 が案件ブロック（新着順に並ぶ）
        for item in soup.select("div.campaignlist1, div.campaignlist2")[:max_items]:
            link = item.select_one("a[href*='UACT=cmpnV']")
            content1 = item.select_one("div.content1")
            if not (link and content1):
                continue
            m = _UID_RE.search(link.get("href", ""))
            if not m:
                continue
            deal_id = m.group(1)

            title = item.select_one("div.title")
            if not title:
                continue

            # ポイントUP時は <s>旧</s> → <span>現</span> の形。旧値を除去し現在値のみ残す
            for old in content1.select("s"):
                old.decompose()
            point = content1.select_one("span")
            if not point:
                continue
            points_text = point.get_text(strip=True)  # 例: "950pt" / "購入金額の3.00%"
            if not points_text:
                continue

            # content1先頭のラベル（会員登録/購入/申込 等）を獲得条件として拾う
            condition = ""
            for node in content1.children:
                text = node.get_text(strip=True) if hasattr(node, "get_text") else str(node).strip()
                if text:
                    condition = text.rstrip("：:")
                    break

            deals.append(self.make_deal(
                deal_id,
                title.get_text(strip=True),
                points_text,
                BASE + "?UACT=cmpnV&USDW=1&UID1=" + deal_id,
                condition,
            ))
        return deals
