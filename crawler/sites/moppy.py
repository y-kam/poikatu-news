"""モッピー — PC新着一覧 ＋ スマホ限定の「アプリ広告カテゴリ」から取得。

アプリ・ゲームのインストール案件はPCサイトには表示されず、スマホ版のアプリ広告
カテゴリ（/ajax/category/get_list.php）にのみ出る。このカテゴリAPIはモバイルUAと
XHRヘッダ(X-Requested-With)の両方が揃って初めて案件を返すため、両方を備えた
専用フェッチャで取得する。PC新着一覧も同じフェッチャで問題なく取得できる。

新着一覧・カテゴリAPIとも1ページ30件。ページ送りはJS（無限スクロール/ページャ）で、
新着は ?page= が効かないため1ページ目を毎日取得する。カテゴリAPIは current_page で
ページ送りでき、child_category を省略すると親カテゴリ単位の全件が返る（2026-07-12実測）
ため、全件バックフィルは有効な全親カテゴリを全ページ巡回する。
"""
import re

from bs4 import BeautifulSoup

from crawler.fetch import PoliteFetcher, MOBILE_UA
from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://pc.moppy.jp/newarrivals/?sorter=new"
DETAIL_URL = "https://pc.moppy.jp/ad/detail.php?site_id={}"  # track_refを除いた正規URL
# スマホ限定「アプリ広告一覧」(parent=4 アプリ・無料登録等 / child=52 アプリ)。HTML断片を返すXHR。
APP_LIST_URL = (
    "https://pc.moppy.jp/ajax/category/get_list.php"
    "?parent_category=4&child_category=52&af_sorter=new&current_page={}"
)
APP_REFERER = "https://pc.moppy.jp/category/list.php?parent_category=4&child_category=52"
# 全件バックフィル用: child_category を省略した親カテゴリ単位の一覧API。
# 有効な親カテゴリは実測で 1〜6・8（7・9は0件。2026-07-12時点。各30件/頁・重複なしでページング可）。
# 1=サービス系 2=クレジットカード 3=金融・口座 4=アプリ・無料登録 5=旅行 6=ショッピング 8=査定・訪問系
PARENT_CATEGORIES = (1, 2, 3, 4, 5, 6, 8)
PARENT_LIST_URL = (
    "https://pc.moppy.jp/ajax/category/get_list.php"
    "?parent_category={}&af_sorter=new&current_page={}"
)
# 一覧リンクの案件ID。PC一覧は site_id=、スマホ一覧は s_id= と表記が異なるため両対応。
_ID_RE = re.compile(r"s(?:ite)?_id=(\d+)")


@register
class MoppyAdapter(SiteAdapter):
    key = "moppy"
    name = "モッピー"

    def make_fetcher(self, interval: float | None = None) -> PoliteFetcher:
        # アプリ広告カテゴリAPIはモバイルUA＋XHRヘッダが必須。PC新着もこの構成で取得可。
        return PoliteFetcher(
            interval=interval or self.request_interval,
            user_agent=MOBILE_UA,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": APP_REFERER},
        )

    def _parse_items(self, html: str, max_items: int) -> list:
        """新着一覧・アプリカテゴリで共通のリスト項目パーサ（同じ m-list__item 構造）。"""
        soup = BeautifulSoup(html, "lxml")
        deals = []
        for item in soup.select("li.m-list__item")[:max_items]:
            link = item.select_one("a.block__link") or item.select_one("a[href*='_id=']")
            title = item.select_one("h3.a-list__item__title")
            point = item.select_one("em.a-list__item__point")
            if not (link and title and point):
                continue  # ポイント対象外案件（point要素なし）はスキップ
            deal_id = _ID_RE.search(link.get("href", ""))
            if not deal_id:
                continue
            condition = item.select_one("p.a-list__item__action")
            deals.append(self.make_deal(
                deal_id.group(1),
                title.get_text(strip=True),
                point.get_text(strip=True),
                DETAIL_URL.format(deal_id.group(1)),
                condition.get_text(strip=True) if condition else "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        # PC新着（クレカ・口座・買い物などPC向け案件）＋ アプリ広告カテゴリ新着（アプリ・ゲーム）
        deals = self._parse_items(fetcher.get(LIST_URL).text, max_items)
        deals += self._parse_items(fetcher.get(APP_LIST_URL.format(1)).text, max_items)
        return deals  # PC/アプリで同一IDが被っても upsert が (site, deal_id) で重複排除する

    # --- 全件バックフィル用: 一覧APIを親カテゴリごとに全ページ巡回する -------------------
    # page_url は単一のページ系列しか表せず複数カテゴリを回れないため、backfill_deals 自体を
    # 実装する。巡回の挙動は base.backfill_deals と同じ（実行内の重複排除・連続2空ページで
    # そのカテゴリを打ち切り・cap 到達で終了・バッチ単位で逐次 yield）。
    def backfill_deals(self, known, cap):
        fetcher = self.make_fetcher()
        seen: set[str] = set()  # カテゴリ間の重複案件を排除（同一案件が複数カテゴリに載る）
        got = 0
        for parent in PARENT_CATEGORIES:
            empty_streak = 0
            for page in range(1, self.max_backfill_pages + 1):
                resp = fetcher.get(PARENT_LIST_URL.format(parent, page))
                fresh = [d for d in self._parse_items(resp.text, 10 ** 9)
                         if d.deal_id and d.deal_id not in seen]
                seen.update(d.deal_id for d in fresh)
                if not fresh:
                    empty_streak += 1
                    if empty_streak >= 2:  # 連続で空ならこのカテゴリの末尾に到達
                        break
                    continue
                empty_streak = 0
                batch = [d for d in fresh if d.deal_id not in known]
                for d in batch:
                    d.backfill = True
                if batch:
                    got += sum(1 for d in batch if d.title)
                    yield batch
                if cap and got >= cap:
                    return
