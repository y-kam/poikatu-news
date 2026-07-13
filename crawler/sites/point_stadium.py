"""ポイントスタジアム — 新着順の案件一覧（SSR・Shift_JIS）から取得。

servicep.asp?s=0 は新着順で1ページ30件。新着が先頭に来るポーリング型のため
1ページのみ取得すればよい（シード不要）。ポイントは div.point_gr の strong に
数値のみ（単位ポイント）で入り、10ポイント=1円（config rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

# c=0100 は全案件対象カテゴリ・s=0 は新着順ソート。pn はページ番号（1のみ取得）
LIST_URL = "https://www.point-stadium.com/servicep.asp?pn=1&c=0100&s=0"
BASE = "https://www.point-stadium.com"
MAX_PAGES = 60  # 全50頁1,495件・30件/頁。安全側に余裕を持たせる

# javascript:funcviewadv('ID') から案件IDを取り出す（IDは英数字混在）
_ID_RE = re.compile(r"funcviewadv\('([^']+)'\)")


@register
class PointStadiumAdapter(SiteAdapter):
    key = "point_stadium"
    name = "ポイントスタジアム"

    def page_url(self, page):
        # LIST_URL の pn=1 を page に差し替える。page==1 は LIST_URL と完全一致。
        if page > MAX_PAGES:
            return None
        return LIST_URL.replace("?pn=1", f"?pn={page}")

    def parse_list(self, resp):
        resp.encoding = "cp932"  # charset宣言はShift_JIS
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for li in soup.select("div.point_box ul.point_get li"):
            link = li.select_one("h3 a")
            gr = li.select_one("div.point_gr")
            if not (link and gr):
                continue
            deal_id = _ID_RE.search(link.get("href", ""))
            point = gr.select_one("strong")
            if not (deal_id and point):
                continue
            points_text = point.get_text(strip=True) + "ポイント"  # strongは数値のみのため単位を補う
            # 獲得条件は div.point_gr p のうち数値span以外の前置きテキスト（例「カード発行で」）
            condition = ""
            p = gr.select_one("p")
            if p:
                for span in p.select("span"):
                    span.decompose()  # 数値ポイント部分を除いて条件文だけ残す
                condition = p.get_text(" ", strip=True)
            deals.append(self.make_deal(
                deal_id.group(1),
                link.get_text(strip=True),
                points_text,
                f"{BASE}/cert.asp?adv={deal_id.group(1)}",  # 詳細（判定/遷移）ページ
                condition,
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
