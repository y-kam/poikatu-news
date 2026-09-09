"""Pontaボーナスパーク — Pontaランク対象の全件一覧 /rank/ ＋ トップ #new 新着枠（SSR）から取得。

トップ https://www.bonuspark.jp/ の `div#new` にiOS/Android/PCの3ワッパーが並び、
それぞれに新着カードが入る（同一案件が複数ワッパーに重複掲載される。約8件）。
新着枠だけでは掲載中案件の大半を取りこぼしていた（2026-09-09監査: 全737件中698件が未取得）
ため、1ページで全件（712件・約1MB）が並ぶ /rank/ を併せて取得し、カタログ型として
ID差分で新着を検知する（初回は apply_seed_policy でシード登録し、新着セクションが溢れる
のを防ぐ。次回クロールで seed_filled として表示解禁される）。
カテゴリ別ページ（/app/ 等18ページ）は /rank/ に載らない約25件のためだけに18リクエスト
増えるため日次では巡回しない。

カードの構造は枠ごとに異なる:
  ランク  a.c-cardList__link … タイトル=.c-cardList__title / ポイント=.c-point__normalText /
          獲得条件=.c-cardList__text
  新着枠  a.c-cardList__card … タイトル=.c-cardList__text / ポイント=.c-point__minText
  （.c-cardList__text の意味が枠で異なるので混同しないこと）
案件URLは /{カテゴリ}/{数字}.html 形式で、末尾の数字が全カテゴリ通し番号の案件ID。
レスポンスヘッダのcharsetが ISO-8859-1 と誤申告されるため utf-8 を明示する。
1P=1円（Pontaポイント）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

TOP_URL = "https://www.bonuspark.jp/"
RANK_URL = "https://www.bonuspark.jp/rank/"  # Pontaランク対象の全案件一覧（1ページ完結）
BASE = "https://www.bonuspark.jp"
# 案件URLは /{カテゴリ}/{数字}.html 形式。末尾の数字が案件ID
_ID_RE = re.compile(r"/(\d+)\.html")


@register
class PontaBonusparkAdapter(SiteAdapter):
    key = "ponta_bonuspark"
    name = "Pontaボーナスパーク"

    def _get_soup(self, fetcher, url):
        resp = fetcher.get(url)
        resp.encoding = "utf-8"  # ヘッダのISO-8859-1誤判定を回避
        return BeautifulSoup(resp.text, "lxml")

    def _add(self, deals: dict, href: str, title_el, point_el, condition_el=None) -> None:
        """カード1件を deals（deal_id→Deal）に登録する。既出IDは先勝ち。"""
        m = _ID_RE.search(href)
        if not (m and title_el and point_el) or m.group(1) in deals:
            return
        deals[m.group(1)] = self.make_deal(
            m.group(1),
            title_el.get_text(strip=True),
            # points_text例: "18,000P"（固定pt=円換算）/ "1%P還元"（%はpercent側）
            point_el.get_text(" ", strip=True),
            href if href.startswith("http") else BASE + href,
            condition_el.get_text(" ", strip=True) if condition_el else "",
        )

    def parse_rank(self, soup, deals: dict) -> None:
        """/rank/ の全件一覧（獲得条件つき）を deals に加える。"""
        for card in soup.select("a.c-cardList__link"):
            self._add(deals, card.get("href", ""),
                      card.select_one(".c-cardList__title"),
                      card.select_one(".c-point__normalText"),
                      card.select_one(".c-cardList__text"))

    def parse_new(self, soup, deals: dict) -> None:
        """トップ #new 新着枠（3ワッパーの和集合）を deals に加える。"""
        for card in soup.select("div#new a.c-cardList__card"):
            self._add(deals, card.get("href", ""),
                      card.select_one(".c-cardList__text"),
                      card.select_one(".c-point__minText"))

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        self.parse_rank(self._get_soup(fetcher, RANK_URL), deals)  # 獲得条件つきのランク版を優先
        self.parse_new(self._get_soup(fetcher, TOP_URL), deals)    # ランク対象外の新着を補う
        # カタログ型はID差分の完全性が必要なため max_items で切らない
        return self.apply_seed_policy(list(deals.values()), known)
