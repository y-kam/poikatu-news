"""LINEブランドカタログ（ec.line.me） — ストア型カタログ（%還元）。

1ページ目はSSR、2ページ目以降はlistmore（JSON内のHTMLフラグメント）。
全店掲載型のため、初回実行はシード登録し新規追加ストアのみを新着として扱う。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://ec.line.me"
LIST_URL = BASE + "/shop/category/all"
MORE_URL = BASE + "/shop/category/all/listmore?sort=RECOMMENDED&async=true&pageNum=true&page={}"
MAX_PAGES = 10  # 実測は701店=6ページ。暴走防止の上限

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_fragment(html: str) -> str:
    """listmoreのhtmlはJSONパース後も \\u003c 形式の二重エスケープが残るため復元する"""
    return _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), html)


@register
class LineCatalogAdapter(SiteAdapter):
    key = "line_catalog"
    name = "LINEブランドカタログ"

    def _parse_cards(self, html: str, deals: dict) -> None:
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("div.shop_card"):
            link = card.select_one("a.shop_link")
            title = card.select_one("strong.title")
            if not (link and title):
                continue
            href = link.get("href", "")
            slug = href.strip("/").split("/")[-1]
            if not slug or slug in deals:
                continue
            rate = link.get("ga-linepointsback-rate", "").strip()
            deals[slug] = self.make_deal(
                slug,
                title.get_text(strip=True),
                rate + "還元" if rate else "",
                href if href.startswith("http") else BASE + href,
            )

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        self._parse_cards(fetcher.get(LIST_URL).text, deals)
        for page in range(2, MAX_PAGES + 1):
            payload = fetcher.get(MORE_URL.format(page)).json()
            result = payload.get("htReturnValue") or {}
            html = result.get("html", "")
            if not html:
                break
            before = len(deals)
            self._parse_cards(_unescape_fragment(html), deals)
            if result.get("last") or len(deals) == before:
                break
        # カタログ型はID差分の完全性が必要なためmax_itemsで切らない（MAX_PAGESで上限済み）
        return self.apply_seed_policy(list(deals.values()), known)
