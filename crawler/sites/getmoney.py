"""GetMoney!（ゲットマネー / dietnavi.com） — サービス案件の新着一覧（SSR・EUC-JP）から取得。

案件一覧 /pc/point/search.php は既定で order=1（新着順）で並ぶため、先頭ページだけを
取得すれば最新案件が得られる（新着順ポーリング型・シード不要）。ポイントUP案件は
p.ico_point 内に span.usually（旧値の <del>）と span.point（現在値）が併存するため、
現在値の span.point のみを読む。10pt=1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

# 既定並び order=1 が新着順。先頭ページ（p指定なし＝1頁目）に最新案件が並ぶ
LIST_URL = "https://dietnavi.com/pc/point/search.php"
BASE = "https://dietnavi.com/pc/"
MAX_PAGES = 12  # 21件/頁・約7頁。安全側に余裕を持たせる
_ID_RE = re.compile(r"ad_detail\.php\?id=(\d+)")


@register
class GetMoneyAdapter(SiteAdapter):
    key = "getmoney"
    name = "GetMoney!"

    def page_url(self, page):
        # pは0始まり（1頁目=p=0）。1頁目は現行LIST_URL文字列そのまま（=p無し・新着順、
        # daily挙動を変えない）、2頁目以降は ?p={page-1}&order=1 で新着順を明示。
        if page > MAX_PAGES:
            return None
        if page == 1:
            return LIST_URL
        return f"{LIST_URL}?p={page - 1}&order=1"

    def parse_list(self, resp):
        resp.encoding = "euc-jp"  # charset宣言がEUC-JPのため明示
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for li in soup.select("div.ad_list li"):
            link = li.select_one("a[href*='ad_detail.php?id=']")
            name = li.select_one("p.name")
            # UP案件は span.usually（旧値）と併存するため現在値の span.point を直接取る
            point = li.select_one("span.point")
            if not (link and name and point):
                continue
            m = _ID_RE.search(link.get("href", ""))
            if not m:
                continue
            href = link["href"]
            deals.append(self.make_deal(
                m.group(1),
                name.get_text(strip=True),
                point.get_text(strip=True),
                href if href.startswith("http") else BASE + href.lstrip("/"),
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
