"""ポイ活倶楽部 — 案件一覧JSON API（新着順1頁ポーリング）から取得。

SSRのHTMLではなく公開JSON API（api.poikatsu.club）を直接叩く。新着が先頭に
来る order_by=newest のため1頁だけ取得すればよく、BeautifulSoupは不要。
ポイントは setting_price（円相当・固定案件）を基本に、refund_rate（%還元系）が
設定されている案件は points_text に「%」を残して percent 側で扱う。
"""
from crawler.sites import register
from crawler.sites.base import SiteAdapter

# 新着順の案件一覧を返すJSON API（Laravel風ページネーション: data.data[]）
API_URL = "https://api.poikatsu.club/api/projects?order_by=newest&page=1&per_page=30"
# 案件詳細ページ（ユーザー向けURL）
DETAIL_URL = "https://poikatsu.club/projects/{}"
MAX_PAGES = 120  # last_page=89。全ページ巡回時の暴走防止上限


@register
class PoikatsuClubAdapter(SiteAdapter):
    key = "poikatsu_club"
    name = "ポイ活倶楽部"

    def page_url(self, page):
        # page=1 は現行dailyのURL（page=1）と完全一致。以降はpageパラメータを差し替え。
        if page > MAX_PAGES:
            return None
        return API_URL.replace("page=1", f"page={page}")

    def parse_list(self, resp):
        data = resp.json()
        # ページネーション構造 data.data[] の中に案件配列が入る
        items = (data.get("data") or {}).get("data") or []
        deals = []
        for it in items:
            deal_id = it.get("id")
            title = it.get("name")
            if deal_id is None or not title:
                continue

            # refund_rate（%還元）が設定されていれば%案件、無ければ setting_price（円相当）
            refund_rate = it.get("refund_rate") or 0
            if float(refund_rate) > 0:
                points_text = f"{refund_rate}%"  # 「%」を残し percent 側で扱わせる
            else:
                points_text = f"{it.get('setting_price') or 0}pt"

            deals.append(self.make_deal(
                deal_id,
                title,
                points_text,
                DETAIL_URL.format(deal_id),
                it.get("condition") or "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        # 日次は新着順1頁目のみ（挙動は従来どおり）
        fetcher = self.make_fetcher()
        return self.parse_list(fetcher.get(self.page_url(1)))[:max_items]
