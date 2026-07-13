"""ポイント広場（ドコモ公式 dポイント広場）— カテゴリ葉ページ巡回＋ID差分。

横断的な新着一覧が存在しないため、サービス系11・ショッピング系14の全カテゴリ葉を
新着順（?sort=recent）で1ページずつ取得して統合する。同一案件が複数カテゴリに
またがって載るためID重複を除去し、全件掲載型（カタログ型）として apply_seed_policy で
初回実行時の大量新着フラッシュを防ぐ。文字コードはヘッダ・meta とも UTF-8 で正しいため
上書きしない。1ポイント＝1円。
"""
from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://hiroba.dpoint.docomo.ne.jp"
CATEGORY_URL = BASE + "/category/{group}/{cat}?sort=recent"

# カテゴリ葉（group, cat）一覧。横断一覧が無いためこれらを個別に巡回する（実サイトで確認）
CATEGORIES = [
    # サービス系（11）
    ("service", "bank"),
    ("service", "beauty"),
    ("service", "community"),
    ("service", "competency"),
    ("service", "coupon"),
    ("service", "creditcard"),
    ("service", "house"),
    ("service", "internet"),
    ("service", "music"),
    ("service", "other"),
    ("service", "travel"),
    # ショッピング系（14）
    ("shopping", "beauty"),
    ("shopping", "book"),
    ("shopping", "electric"),
    ("shopping", "fashion"),
    ("shopping", "gift"),
    ("shopping", "gourmet"),
    ("shopping", "grocery"),
    ("shopping", "health"),
    ("shopping", "interior"),
    ("shopping", "kids"),
    ("shopping", "mailorder"),
    ("shopping", "other"),
    ("shopping", "pet"),
    ("shopping", "sports"),
]


@register
class PointHirobaAdapter(SiteAdapter):
    key = "point_hiroba"
    name = "ポイント広場"
    request_interval = 5.0  # 25カテゴリを巡回するため間隔を短縮（それでも全体で数分）

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        for group, cat in CATEGORIES:
            url = CATEGORY_URL.format(group=group, cat=cat)
            try:
                # ヘッダ・meta とも UTF-8 で正しいため encoding は上書きしない
                soup = BeautifulSoup(fetcher.get(url).text, "lxml")
            except Exception:
                continue  # 個別カテゴリの失敗は全体を止めない
            for item in soup.select("li.l-card__item"):
                anchor = item.select_one("a.c-card__anchor")
                title = item.select_one("p.c-card__ttl")
                point = item.select_one("em.c-incentive-point")
                if not (anchor and title and point):
                    continue
                deal_id = anchor.get("data-item-id")
                if not deal_id or deal_id in deals:
                    continue  # 複数カテゴリ横断の重複を除去
                # ポイントUP時は通常値 s.c-af-incentive__usual が併記されるため除去して現在値のみ残す
                usual = item.select_one("s.c-af-incentive__usual")
                if usual:
                    usual.decompose()
                # em内テキスト（固定pt="4,000 P" / %型="1.5%"）をそのまま渡す。
                # %を含めば percent、含まなければ数値×rate=円 として parse_points が判定する
                points_text = point.get_text(" ", strip=True)
                condition = item.select_one("span.label-require")
                href = anchor.get("href", "")
                deals[deal_id] = self.make_deal(
                    deal_id,
                    title.get_text(strip=True),
                    points_text,
                    href if href.startswith("http") else BASE + href,
                    condition.get_text(" ", strip=True) if condition else "",
                )
        # 新着順横断一覧が無い全件掲載型のため、ID差分の完全性が必要でmax_itemsで切らない
        return self.apply_seed_policy(list(deals.values()), known)
