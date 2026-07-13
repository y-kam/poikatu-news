"""ポイントインカム — PC新着ajax ＋ スマホ版アプリ一覧(sp.pointi.jp)から取得。

アプリ・ゲームのインストール案件はPC一覧(ajax_load/load_list.php)には出ず、スマホ
専用サイト sp.pointi.jp のアプリ枠にのみ並ぶ。sp のアプリ一覧はモバイルUAとXHR
ヘッダが要る。日次はアプリ新着(load_arrival_ad.php?rf=1)、全件バックフィルはアプリ
各サブカテゴリ(pts_app.php?cat_no=...)を巡回する。deal_id は /ad/NN/ でPC・spとも
共通のため site:deal_id で自然に重複排除される。
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler.fetch import PoliteFetcher, MOBILE_UA
from crawler.sites import register
from crawler.sites.base import SiteAdapter

AJAX_URL = "https://pointi.jp/ajax_load/load_list.php?order=1&page=1&max=20&narrow=0&mode=1"
BASE = "https://pointi.jp/"
SP_BASE = "https://sp.pointi.jp/"
SP_ARRIVAL_URL = "https://sp.pointi.jp/ajax_load/load_arrival_ad.php?rf=1"  # アプリ新着タブ
SP_APP_CAT_URL = "https://sp.pointi.jp/pts_app.php?cat_no={}&sort=&sub=4"   # アプリ各サブカテゴリ
# アプリ枠のサブカテゴリID群（RPG/パズル等）。全件バックフィルはこれらを巡回する。
APP_CAT_NOS = list(range(285, 303))
_ID_RE = re.compile(r"/ad/(\d+)")


@register
class PointIncomeAdapter(SiteAdapter):
    key = "pointincome"
    name = "ポイントインカム"

    def _sp_fetcher(self) -> PoliteFetcher:
        # sp のアプリ一覧はモバイルUA＋XHRヘッダが必須
        return PoliteFetcher(
            interval=self.request_interval, user_agent=MOBILE_UA,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": SP_BASE + "app/"},
        )

    def _parse_pc(self, resp, max_items):
        """PC新着ajax（Shift_JISフラグメント）のパーサ。"""
        resp.encoding = "cp932"  # 明示しないと文字化け
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div.box_ad_wrap")[:max_items]:
            link = item.select_one("a.cont_img")
            title = item.select_one("div.title_list")
            point = item.select_one("div.list_pt")
            if not (link and title and point):
                continue
            m = _ID_RE.search(link.get("href", ""))
            if not m:
                continue
            deals.append(self.make_deal(
                m.group(1), title.get_text(strip=True),
                point.get_text("", strip=True), urljoin(BASE, link["href"])))
        return deals

    def _parse_sp(self, resp, max_items):
        """sp アプリ一覧のパーサ。新着フラグメントは p.title/p.point、カテゴリページ
        (pts_app)は div.title/div.point とタグが異なるため、クラスのみで拾う。"""
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for a in soup.select("a.pi_a[href*='/ad/']")[:max_items]:
            m = _ID_RE.search(a.get("href", ""))
            title = a.select_one(".title")
            point = a.select_one(".point")
            if not (m and title and point):
                continue
            deals.append(self.make_deal(
                m.group(1), title.get_text(strip=True),
                point.get_text(" ", strip=True), urljoin(SP_BASE, a["href"])))
        return deals

    def fetch_deals(self, known, max_items):
        deals = self._parse_pc(self.make_fetcher().get(AJAX_URL), max_items)      # PC新着
        deals += self._parse_sp(self._sp_fetcher().get(SP_ARRIVAL_URL), max_items)  # アプリ新着
        return deals  # PC/spで同一 deal_id が被っても upsert が (site, deal_id) で重複排除

    def backfill_deals(self, known, cap):
        """全件バックフィル: アプリ各サブカテゴリを巡回（sp専用フェッチャ）。"""
        fetcher = self._sp_fetcher()
        seen: set[str] = set()
        got = 0
        for cat in APP_CAT_NOS:
            items = self._parse_sp(fetcher.get(SP_APP_CAT_URL.format(cat)), 10 ** 9)
            fresh = [d for d in items if d.deal_id not in seen]
            seen.update(d.deal_id for d in fresh)
            batch = [d for d in fresh if d.deal_id not in known]
            for d in batch:
                d.backfill = True
            if batch:
                got += sum(1 for d in batch if d.title)
                yield batch
            if cap and got >= cap:
                break
