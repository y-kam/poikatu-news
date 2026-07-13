"""Powl — ジャンル別カタログ（SSR・UTF-8）の全ジャンル巡回＋ID差分。

新着順ソートが存在しないカタログ型のため、ジャンル（中項目）ページを順に取得して
全案件を集め、既知IDとの差分で新着を検知する（初回実行はシード登録）。
各ジャンルページはSSRで全件を一度に返す（サーバ側ページングは無い）ため、
巡回対象はジャンルIDの固定リストのみ。ジャンル間でIDが重複するため辞書で排除する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://web.powl.jp"
GENRE_URL = BASE + "/genre/{}"

# 巡回対象の中項目ジャンルID（クレカ/口座/EC/サービス等）。199/201 は大きな
# 内包カテゴリで他ジャンルと重複するが、ID重複排除で吸収されるため取りこぼし防止に含める。
GENRE_IDS = (
    101, 102, 103, 104, 105, 106, 199,
    201, 202,
    301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 399,
)
MAX_GENRES = 30  # 巡回ジャンル数の上限（設定ミス等での暴走防止。実リストはこれ未満）

_ID_RE = re.compile(r"/reward/(\d+)")


@register
class PowlAdapter(SiteAdapter):
    key = "powl"
    name = "Powl"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}  # deal_id -> Deal（ジャンル間の重複IDを排除）
        for gid in GENRE_IDS[:MAX_GENRES]:
            try:
                resp = fetcher.get(GENRE_URL.format(gid))
            except Exception:
                continue  # 個別ジャンルの取得失敗は全体を止めない
            soup = BeautifulSoup(resp.text, "lxml")
            for item in soup.select("a.search-result-simple-list-item"):
                href = item.get("href", "")
                m = _ID_RE.search(href)  # /reward/{id} 末尾数値が案件ID
                if not m or m.group(1) in deals:
                    continue
                title_el = item.select_one("p.search-result-simple-list-title")
                point_el = item.select_one("p.search-result-simple-list-point")
                if not (title_el and point_el):
                    continue
                deal_id = m.group(1)
                # point表記は「45,000pt」(固定) か「1.3%還元」(％)。%はparse_pointsが％側に振る。
                deals[deal_id] = self.make_deal(
                    deal_id,
                    title_el.get_text(strip=True),
                    point_el.get_text(strip=True),
                    href if href.startswith("http") else BASE + href,
                )
        # カタログ型はID差分の完全性が必要なためmax_itemsで切らず全件返す
        return self.apply_seed_policy(list(deals.values()), known)
