"""チャンスイット — 新着順ポイント一覧（SSR）から取得。

現行ドメインは www.chance.com（旧 chanceit.jp は稼働停止）。
一覧は /point/?order=1 で新着順に並ぶため1頁だけ取得すればよい（シード不要）。
ポイントUP案件は del(旧値)→ins(現在値)形式のため、旧値を除去して現在値を取る。
%還元（100円ごと等）の案件は一覧では還元率が「-」表記でポイント無しのため、値なしのまま渡す。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.chance.com/point/?order=1&limit=20"  # 新着順1頁
BASE = "https://www.chance.com"
MAX_PAGES = 60  # 593件・20件/頁 ≈ 30頁。安全側に余裕を持たせる

_ID_RE = re.compile(r"[?&]id=(\d+)")  # 詳細URL（/items/detail.jsp?id=）から案件ID抽出


@register
class ChanceitAdapter(SiteAdapter):
    key = "chanceit"
    name = "チャンスイット"

    def page_url(self, page):
        # チャンスイットの page は0始まり（1頁目=page=0）。全件バックフィルで全頁巡回する。
        if page > MAX_PAGES:
            return None
        return f"{LIST_URL}&page={page - 1}"

    def parse_list(self, resp):
        resp.encoding = "utf-8"  # charset宣言はUTF-8
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("ul.list > li"):
            link = item.select_one('a[href*="items/detail.jsp"]')
            name = item.select_one("p.name")
            point = item.select_one("p.point")
            if not (link and name and point):
                continue
            deal_id = _ID_RE.search(link.get("href", ""))
            if not deal_id:
                continue
            # UP案件は del要素に旧値が入るため除去し現在値（ins）のみ残す
            for old in point.select("del"):
                old.decompose()
            points_text = point.get_text(" ", strip=True)
            condition = item.select_one("p.condition")
            deals.append(self.make_deal(
                deal_id.group(1),
                name.get_text(strip=True),
                points_text,
                BASE + link["href"] if not link["href"].startswith("http") else link["href"],
                condition.get_text(" ", strip=True) if condition else "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
