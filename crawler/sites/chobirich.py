"""ちょびリッチ — サービス系・ショッピング系の新着一覧（SSR）＋ スマホ「アプリで貯める」。

アプリ・ゲームのインストール案件はPCの新着一覧には出ず、スマホ版の「アプリで貯める」
(/smartphone/) にのみ並ぶ。/smartphone/ はPC UAだと空のシェルを返すため、モバイルUAで
取得する（項目構造もPC一覧とは別）。deal_id は /ad_details/NN で共通のため重複排除される。

2026-11 のリニューアル（スマホ版 11/04ごろ・PC版 11/11ごろ）で、意味のあるclass名
（ad-category__ad 等）が Tailwind のユーティリティclassに置き換わった。ユーティリティclassは
デザイン調整だけで変わるため、案件の特定は「/ad_details/ へのリンクの中に見出しがある」という
構造で行い、class名は色の意味を持つ text-attention（ポイント欄）だけに頼る。
"""
import re

from bs4 import BeautifulSoup

from crawler.fetch import PoliteFetcher, MOBILE_UA
from crawler.sites import register
from crawler.sites.base import Deal, SiteAdapter

# 旧URL（/earn/new_list・/shopping/new_list/）はリニューアル後ここへ転送される。
# 転送に頼らず移転先を直接取得する。1ページ30件（2頁目以降は &page=N）。
LIST_URLS = (
    "https://www.chobirich.com/search?category=earn&feature=new",
    "https://www.chobirich.com/search?category=shopping&feature=new",
)
APP_URL = "https://www.chobirich.com/smartphone/"  # アプリで貯める（モバイルUA必須）
BASE = "https://www.chobirich.com"
# 一覧リンクの案件ID。通常は /ad_details/NN、ステップアップ案件は /ad_details/redirect/NN。
# サイドバーの会員向けコンテンツ（/ad_details/member_only/NN）は案件ではないので一致させない。
_ID_RE = re.compile(r"/ad_details/(?:redirect/)?(\d+)")
POINT_SELECTOR = ".text-attention"  # ポイント欄（NEWバッジは bg-attention で別物）


def _canonical_url(deal_id: str) -> str:
    # 一覧のhrefは末尾スラッシュ無し・redirect/経由など揺れるため、保存済みデータと同じ形にそろえる
    return f"{BASE}/ad_details/{deal_id}/"


def _point_text(point) -> str:
    # ポイントUP案件は <s>旧値</s> を除去して現在値だけ残す（他サイトのアダプタと同じ扱い）
    for old in point.select("s"):
        old.decompose()
    return point.get_text("", strip=True)


@register
class ChobirichAdapter(SiteAdapter):
    key = "chobirich"
    name = "ちょびリッチ"

    # 403 Forbidden 対策: トップからの遷移に見せる。Referer を伴うため
    # Sec-Fetch-Site を同一オリジン遷移（same-origin）に整合させる。
    extra_headers = {
        "Referer": BASE + "/",
        "Sec-Fetch-Site": "same-origin",
    }
    # 403 が断続的に発生するため、間隔を空けて最大2回まで再試行する（リストURLは軽い）
    max_retries = 2

    def _mobile_fetcher(self) -> PoliteFetcher:
        # スマホ「アプリで貯める」はモバイルUAが必須（PC UAだと空シェルが返る）
        return PoliteFetcher(
            interval=self.request_interval, user_agent=MOBILE_UA,
            headers=self.extra_headers, max_retries=self.max_retries)

    def parse_pc_list(self, html: str) -> list[Deal]:
        """PC新着一覧のHTMLから案件を抽出する。案件カードは「/ad_details/ へのリンクの中に
        h3（案件名）がある」もの。カードは「新規掲載」バッジ（list_new_markers）もリンク内に持つ。"""
        deals = []
        for link in BeautifulSoup(html, "lxml").select("a[href*='/ad_details/']"):
            title = link.select_one("h3")
            point = link.select_one(POINT_SELECTOR)
            m = _ID_RE.search(link.get("href", ""))
            if not (title and point and m):
                continue
            item_html = str(link)  # バッジ判定はポイント欄の加工前のカードHTMLで行う
            deal = self.make_deal(
                m.group(1), title.get_text(strip=True), _point_text(point),
                _canonical_url(m.group(1)))
            # 「新規掲載」バッジ → 再新着判定（Skill new-marker-audit の経路③）
            deals.append(self.flag_site_new(deal, item_html))
        return deals

    def parse_app_list(self, html: str) -> list[Deal]:
        """スマホ「アプリで貯める」のHTMLから案件を抽出する。案件は「/ad_details/ への
        リンクの中に h2（アプリ名）がある」もの。ポイント欄の後の段落が獲得条件。
        上部のオファーウォール（バナー画像だけのリンク）は見出しが無いので除外される。"""
        deals = []
        for link in BeautifulSoup(html, "lxml").select("a[href*='/ad_details/']"):
            title = link.select_one("h2")
            point = link.select_one(POINT_SELECTOR)
            m = _ID_RE.search(link.get("href", ""))
            if not (title and point and m):
                continue
            condition = next((p for p in link.select("p") if p is not point), None)
            deals.append(self.make_deal(
                m.group(1), title.get_text(strip=True), _point_text(point),
                _canonical_url(m.group(1)),
                condition.get_text(strip=True) if condition else "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        pages = [self.parse_pc_list(fetcher.get(url).text) for url in LIST_URLS]  # PC新着
        # スマホ「アプリで貯める」（アプリ・ゲームDL案件。PC一覧には出ない）
        pages.append(self.parse_app_list(self._mobile_fetcher().get(APP_URL).text))
        deals = {}
        for page in pages:
            for deal in page:
                deals.setdefault(deal.deal_id, deal)  # 複数一覧に載る案件は先に出た方を採る
        return list(deals.values())[:max_items]
