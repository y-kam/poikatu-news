"""アメフリ — 案件一覧（SSR）を新着順で1ページ取得。

新着順ソート（sort=-start）の先頭ページに最新案件が並ぶポーリング型のため、
1ページだけ取得してそのまま返す（シード不要）。ポイントアップ中の案件は
元値（point--base）と現在値（point--emphasis）が併記されるため、常に現在値
（認証済の強調表示）側を採用する。10pt=1円（rate=0.1）。

アプリ・ゲームのインストール案件は asp_device=pc 表示には出ず、asp_device=sp
（スマホ表示）にのみ並ぶ（UAはPCのままでよい）。pc表示（クレカ・買い物等）と
sp表示（アプリ）は排他なので、日次は両方の新着1頁を取得し、バックフィルは
アプリを取りこぼさないよう sp 表示を全ページ巡回する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

PC_LIST_URL = "https://point.i2i.jp/item_list?asp_device=pc&sort=-start"
SP_LIST_URL = "https://point.i2i.jp/item_list?asp_device=sp&sort=-start"
BASE = "https://www.amefri.net"
MAX_PAGES = 210  # 15件/頁・約199頁。安全側に余裕を持たせる
_ID_RE = re.compile(r"/detail/id/(\d+)")


@register
class AmefriAdapter(SiteAdapter):
    key = "amefri"
    name = "アメフリ"

    def page_url(self, page):
        # バックフィルはアプリ案件（sp表示）を全ページ巡回する。pc表示の非アプリ案件は
        # 既に取得済みで日次でも更新されるため、ここでは sp 表示を対象にする。
        if page > MAX_PAGES:
            return None
        if page == 1:
            return SP_LIST_URL
        return f"{SP_LIST_URL}&page={page}"

    def parse_list(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div.projectList__item"):
            link = item.select_one("a.cardBlock__blockLink")
            name = item.select_one("p.projectList__name")
            if not (link and name):
                continue
            m = _ID_RE.search(link.get("href", ""))
            if not m:
                continue

            # ポイントアップ中は point--base（元値）が併存するため強調表示の現在値を採る。
            # 単位（pt / ％）は末尾のspan.unitに入るので現在値と一緒に拾う（％案件の判別に必須）。
            emphasis = item.select_one("span.point--emphasis")
            if not emphasis:  # 強調表示が無い構造への保険として元値を使う
                emphasis = item.select_one("div.pointWrapper--base span.point")
            if not emphasis:
                continue
            unit = emphasis.find_next_sibling("span", class_="unit")
            points_text = emphasis.get_text(strip=True) + (unit.get_text(strip=True) if unit else "")

            condition = item.select_one("td.table-data")
            deal = self.make_deal(
                m.group(1),
                name.get_text(strip=True),
                points_text,
                BASE + m.group(0),
                condition.get_text(strip=True) if condition else "",
            )
            # NEW!（labelArea__item--new）・ポイントアップ中!!（--ptup）ラベル→再新着判定
            deals.append(self.flag_site_new(deal, str(item)))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次はpc表示（クレカ・買い物等）とsp表示（アプリ・ゲーム）の新着1頁ずつ
        fetcher = self.make_fetcher()
        deals = self.parse_list(fetcher.get(PC_LIST_URL))[:max_items]
        deals += self.parse_list(fetcher.get(SP_LIST_URL))[:max_items]
        return deals
