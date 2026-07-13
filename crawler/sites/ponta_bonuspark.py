"""Pontaボーナスパーク — トップページ #new 新着枠（SSR）から取得。

トップ https://www.bonuspark.jp/ の `div#new` にiOS/Android/PCの3ワッパーが
並び、それぞれに新着カードが入る（同一案件が複数ワッパーに重複掲載される）。
新着が先頭に来るポーリング型のため、3ワッパーの和集合から重複IDを排除して返す
（シード不要）。カテゴリ別ページの巡回は任意で、MVPはトップ#newのみとする。

レスポンスヘッダのcharsetが ISO-8859-1 と誤申告されるため utf-8 を明示する。
1P=1円（Pontaポイント）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

TOP_URL = "https://www.bonuspark.jp/"
BASE = "https://www.bonuspark.jp"
# 案件URLは /{カテゴリ}/{数字}.html 形式。末尾の数字が案件ID
_ID_RE = re.compile(r"/(\d+)\.html")


@register
class PontaBonusparkAdapter(SiteAdapter):
    key = "ponta_bonuspark"
    name = "Pontaボーナスパーク"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        resp = fetcher.get(TOP_URL)
        resp.encoding = "utf-8"  # ヘッダのISO-8859-1誤判定を回避
        soup = BeautifulSoup(resp.text, "lxml")

        deals = []
        seen: set[str] = set()  # 3ワッパー間の重複ID排除
        for card in soup.select("div#new a.c-cardList__card"):
            href = card.get("href", "")
            m = _ID_RE.search(href)
            if not m:
                continue
            deal_id = m.group(1)
            if deal_id in seen:
                continue
            title_el = card.select_one(".c-cardList__text")
            point_el = card.select_one(".c-point__minText")
            if not (title_el and point_el):
                continue
            seen.add(deal_id)
            # points_text例: "18,000P"（固定pt=円換算）/ "1%P還元"（%はpercent側）
            deals.append(self.make_deal(
                deal_id,
                title_el.get_text(strip=True),
                point_el.get_text(" ", strip=True),
                href if href.startswith("http") else BASE + href,
            ))
            if len(deals) >= max_items:
                break
        return deals
