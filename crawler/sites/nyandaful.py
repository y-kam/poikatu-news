"""懸賞にゃんダフル — 新着順一覧（SSR・Shift_JIS）から取得。

plist.asp?pur=1 が新着順の1ページ（ちょうど25件）を返す。新着が先頭に来る
ポーリング型のためシード処理は不要で、1ページだけ取得してそのまま返す。
レスポンスヘッダにcharset宣言が無い（Shift_JIS実体）ため cp932 を明示する。
10pt=1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.nyandaful.jp/plist.asp?pur=1"  # 新着順一覧（25件）
# page>=2 のページングURL（p= を差し替え）。新着順25件/頁で全60頁。
PAGE_URL = "https://www.nyandaful.jp/plist.asp?qt=1&cate=&pur=1&point_keyword=&p={page}"
MAX_PAGES = 70  # 全60頁・25件/頁。安全側に余裕を持たせる
# p_detail.asp?m=<案件ID> の m パラメータを案件IDとして抽出する
_ID_RE = re.compile(r"[?&]m=([^&]+)")


@register
class NyandafulAdapter(SiteAdapter):
    key = "nyandaful"
    name = "懸賞にゃんダフル"

    def page_url(self, page):
        # page==1 は現行の新着順LIST_URLをそのまま返す（daily挙動を変えない）。
        # page>=2 はページ番号付きの一覧URLを返す。
        if page > MAX_PAGES:
            return None
        if page == 1:
            return LIST_URL
        return PAGE_URL.format(page=page)

    def parse_list(self, resp):
        resp.encoding = "cp932"  # charset宣言が無いShift_JISのため明示
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        # 各案件カードは div.col-xs-12 直下の a.pickup（ちょうど25件）
        for a in soup.select("div.col-xs-12 > a.pickup"):
            href = a.get("href", "")
            m = _ID_RE.search(href)
            if not m:
                continue
            deal_id = m.group(1)

            h4 = a.select_one("h4")
            if not h4:
                continue
            item_html = str(a)  # NEW/UPバッジ判定用。バッジをdecomposeする前に退避する
            for badge in h4.select("span.badge"):
                badge.decompose()  # 「New」バッジを除去してタイトルのみ残す
            title = h4.get_text(" ", strip=True)

            btn = a.select_one("button.btn.btn-present")
            if not (title and btn):
                continue
            points_text = btn.get_text(" ", strip=True)  # 例 "130,000pt"（10pt=1円）

            # 獲得条件はボタンを含む div.small 内（例「回線開通で」「カード発行で」）
            condition = ""
            small = a.select_one("div.small:has(button.btn-present)")
            if small:
                for x in small.select("span.badge-ke-p-b, button"):
                    x.decompose()  # 「P」バッジと還元pt表記を除去し条件文だけ残す
                condition = small.get_text(" ", strip=True)

            deal = self.make_deal(
                deal_id,
                title,
                points_text,
                href if href.startswith("http") else "https://www.nyandaful.jp/" + href.lstrip("/"),
                condition,
            )
            deals.append(self.flag_site_new(deal, item_html))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
