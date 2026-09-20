"""げん玉 — カテゴリ別一覧（SSR・Shift_JIS）の全カテゴリ巡回＋ID差分（カタログ型）。

サイト横断の新着一覧が無い（SP版 /sp/new_services は robots.txt で Disallow）ため、
カテゴリ一覧 `/service/category/{cat}/13`（末尾 13=新着順 / 14=ポイント数順 / 15=人気順）を
巡回し、既知IDとの差分で新着を検知する。2頁目以降はオフセット式
`/service/category/{cat}/13/0/{offset}`（40件/頁。末尾を越えると0件）。
日次は基本カテゴリ（サービス系12＋ショッピング系9）の新着順1頁目のみ（21リクエスト）。
属性別の一覧（ポイントアップ・3000pt以上・速判定 等）は基本カテゴリの部分集合なので
日次では回らず、全件バックフィルでのみ念のため巡回する（重複はIDで排除）。

一覧の案件名は長いと「...」で省略されるため、省略名の案件は詳細ページ `/service/item/{id}` の
h1 から省略無しの名前を取る（日次は未知の案件のみ・上限つき。既知の案件は省略名で
既存の名前を上書きしないよう title を空にして返し、store.upsert に既存値を保たせる）。
初回（未知が多数）は apply_seed_policy でシード登録し、全件バックフィルで名前と表示を埋める。
ポイント表記は `dd.pt`（"633pt" / "購入金額の4％分のポイント"。UP案件は `<s>旧値</s> 新値`）。
掲載終了ページは200を返すソフト404（本文「このサービスは終了いたしました」→ dead_markers）。
10pt=1円（rate=0.1。詳細ページの「633pt(63円相当)」表記で確認）。2026-09-17調査。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import Deal, SiteAdapter

BASE = "https://www.gendama.jp"
LIST_URL = BASE + "/service/category/{cat}/13"               # 新着順1頁目
PAGE_URL = BASE + "/service/category/{cat}/13/0/{offset}"    # 2頁目以降（オフセット式）
DETAIL_URL = BASE + "/service/item/{id}"
PER_PAGE = 40
# 日次で巡回する基本カテゴリ（サービス系＋ショッピング系）
DAILY_CATEGORIES = (
    "freejoin", "chargejoin", "campaign", "demand", "quick", "credit_card", "loan_cards",
    "account", "immediate_approval", "buy_item", "estimate", "others",
    "gourmet", "fashion", "health", "hobby", "he_pc", "household_goods", "pet_items", "total", "catalog",
)
# 全件バックフィルでは属性別の一覧も含めて巡回する（取りこぼし防止）
ALL_CATEGORIES = DAILY_CATEGORIES + (
    "fast", "half_return", "full_return", "point_service", "pointup", "ippatsu", "standard",
)
MAX_PAGES_PER_CATEGORY = 60  # 40件/頁。2026-09-17時点の最大は others の17頁超。暴走防止上限
MAX_DETAIL_FETCH = 30        # 日次で省略無しの案件名を詳細ページから取る上限（負荷抑制）
TRUNCATED = "..."            # 一覧で省略された案件名の末尾
_ID_RE = re.compile(r"/service/item/(\d+)")


@register
class GendamaAdapter(SiteAdapter):
    key = "gendama"
    name = "げん玉"

    def _get(self, fetcher, url):
        resp = fetcher.get(url)
        resp.encoding = "cp932"  # Shift_JIS（ヘッダは ISO-8859-1 と誤申告）
        return resp

    def _category_url(self, cat: str, page: int) -> str:
        if page == 1:
            return LIST_URL.format(cat=cat)
        return PAGE_URL.format(cat=cat, offset=(page - 1) * PER_PAGE)

    def parse_list(self, resp) -> list[Deal]:
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div.section_item"):
            link = item.select_one("a.link_item[href]")
            title = item.select_one("dl.list_info01 dt")
            point = item.select_one("dd.pt")
            if not (link and title and point):
                continue
            m = _ID_RE.search(link["href"])
            if not m:
                continue
            for old in point.select("s"):
                old.decompose()  # UP案件の旧値（<s>1,500pt</s>）を除去し現在値のみ残す
            condition = item.select_one("dd.condition")
            if condition:
                for label in condition.select("span"):
                    label.decompose()  # 見出し「条件」を除く
            deals.append(self.make_deal(
                m.group(1),
                title.get_text(strip=True),
                point.get_text(" ", strip=True),
                DETAIL_URL.format(id=m.group(1)),  # frame= 等の一覧由来パラメータは持たない
                condition.get_text(" ", strip=True) if condition else "",
            ))
        return deals

    def _fill_title(self, fetcher, deal: Deal) -> None:
        """一覧で省略された案件名を詳細ページの h1 で置き換える（取得失敗時は省略名のまま）。"""
        if not deal.title.endswith(TRUNCATED):
            return
        try:
            soup = BeautifulSoup(self._get(fetcher, deal.url).text, "lxml")
        except Exception:
            return
        h1 = soup.select_one("h1")
        full = h1.get_text(strip=True) if h1 else ""
        if full:
            deal.title = full

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        for cat in DAILY_CATEGORIES:
            for deal in self.parse_list(self._get(fetcher, self._category_url(cat, 1))):
                deals.setdefault(deal.deal_id, deal)  # カテゴリ間の重複掲載はIDで排除
        items = self.apply_seed_policy(list(deals.values()), known)
        fetched = 0
        for deal in items:
            if not deal.title.endswith(TRUNCATED):
                continue
            if deal.deal_id in known:
                deal.title = ""  # 既存の（省略無しの）名前を省略名で上書きしない
                deal.title_kept = True  # 意図的な空欄（パーサ破損と区別する印）
            elif not deal.seeded and fetched < MAX_DETAIL_FETCH:
                self._fill_title(fetcher, deal)
                fetched += 1
        return items

    # --- 全件バックフィル用: 全カテゴリを全ページ巡回し、省略名は詳細ページで補完する --------
    # 巡回の挙動は base.backfill_deals と同じ（実行内の重複排除・連続2空ページで打ち切り・
    # cap 到達で終了・バッチ単位で逐次 yield）。
    def backfill_deals(self, known, cap):
        fetcher = self.make_fetcher()
        seen: set[str] = set()
        got = 0
        for cat in ALL_CATEGORIES:
            empty_streak = 0
            for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
                items = self.parse_list(self._get(fetcher, self._category_url(cat, page)))
                fresh = [d for d in items if d.deal_id not in seen]
                seen.update(d.deal_id for d in fresh)
                if not fresh:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                    continue
                empty_streak = 0
                batch = [d for d in fresh if d.deal_id not in known]
                for d in batch:
                    d.backfill = True
                    self._fill_title(fetcher, d)
                if batch:
                    got += len(batch)
                    yield batch
                if cap and got >= cap:
                    return
