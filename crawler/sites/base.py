"""サイトアダプタの基底クラスと案件データモデル。"""
import gzip
import io
import re
from dataclasses import dataclass
from xml.etree import ElementTree

from crawler.fetch import PoliteFetcher
from crawler.normalize import parse_points


@dataclass
class Deal:
    site: str
    deal_id: str
    title: str
    points_text: str
    yen: float | None       # 円換算の還元額（固定pt案件）
    percent: float | None   # %還元の案件（円換算不能）
    url: str
    condition: str = ""     # 獲得条件テキスト（カテゴリ分類・表示に使用）
    seeded: bool = False    # 初回実行時にIDだけ登録した案件（表示対象外）
    backfill: bool = False  # 一度きりの全件バックフィルで取得した案件（後で一括削除できる印）
    first_seen_override: str | None = None  # 出の日付が分かる場合の初出日（不明ならNone→2000-01-01）
    # 一覧アイテムにサイト側のNEW/UPバッジが付いているか（list_new_markers設定サイトのみ。
    # None=判定対象外。既知案件でTrueなら再新着＝ポイントUP扱いにする。store.upsert が参照）
    site_new: bool | None = None


class SiteAdapter:
    key: str = ""
    name: str = ""
    request_interval: float = 10.0
    extra_headers: dict = {}  # サイト固有の追加/上書きHTTPヘッダ（例: Referer。403対策）
    max_retries: int = 0      # 断続的な 403/429/503 の再試行回数（0で再試行しない）

    def __init__(self, config: dict):
        self.config = config
        self.rate: float = config["rate"]  # 1ポイントの円価値

    def make_fetcher(self, interval: float | None = None) -> PoliteFetcher:
        return PoliteFetcher(
            interval=interval or self.request_interval,
            headers=self.extra_headers or None,
            max_retries=self.max_retries,
        )

    def make_deal(self, deal_id: str, title: str, points_text: str, url: str,
                  condition: str = "") -> Deal:
        yen, percent = parse_points(points_text, self.rate)
        return Deal(
            site=self.key, deal_id=str(deal_id), title=title.strip(),
            points_text=points_text.strip(), yen=yen, percent=percent, url=url,
            condition=condition.strip(),
        )

    def flag_site_new(self, deal: Deal, item_html: str) -> Deal:
        """一覧アイテムのHTMLにサイト側のNEW/UPバッジ（config: list_new_markers）が
        含まれるかを deal.site_new に記録する（マーカー未設定サイトは None のまま）。
        アイテム単位のHTMLと照合することで、検索フィルタ・サイドバー等のページ共通UIに
        同じ文字列があっても誤検知しない。バッジ調査手順は Skill `new-marker-audit`。"""
        markers = self.config.get("list_new_markers")
        if markers:
            deal.site_new = any(m in item_html for m in markers)
        return deal

    def fetch_deals(self, known: set[str], max_items: int) -> list[Deal]:
        """新着案件を返す。known は既知 deal_id の集合（sitemap差分方式で使用）"""
        raise NotImplementedError

    # --- 一度きりの全件バックフィル用 ---------------------------------------
    # 全件取得はページング可能なサイトでのみ成立する。各アダプタは
    #   page_url(page) : Nページ目のURL（1始まり。無ければNone＝ページング終了）
    #   parse_list(resp): レスポンスから案件を抽出（fetch_deals と共有する想定）
    # を実装すると、下の backfill_deals が全ページを巡回する。
    # 未対応（page_url が None）のサイトは fetch_deals の1ページ分だけを返す。
    max_backfill_pages: int = 500  # 暴走防止の安全上限

    def page_url(self, page: int) -> str | None:
        """バックフィル時のNページ目URL。Noneを返すと全件不可（1ページのみ）とみなす。"""
        return None

    def parse_list(self, resp) -> list[Deal]:
        """一覧レスポンスから案件リストを抽出する（page_url実装時に必須）。"""
        raise NotImplementedError

    def backfill_deals(self, known: set[str], cap: int):
        """全案件を「バッチ（Dealのリスト）」単位で yield するジェネレータ。

        バッチごとに逐次保存・中断できるよう、まとめてreturnせず小分けにyieldする。
        known は既に詳細取得済み（タイトル有り）のID集合で、再開時に再取得を省くために使う。
        cap は1サイトあたりの新規取得上限（0で無制限）。
        """
        if self.page_url(1) is None:
            # ページング未対応サイト → 現行の1ページ取得分だけをバックフィル扱いで返す。
            # カタログ型（1リクエストで全件返す rebates/powl 等）はこの経路で全件が入るため、
            # cap=0（無制限）を 300 に丸めない（丸めるとシード滞留分が埋まらず掲載漏れになる）
            deals = [d for d in self.fetch_deals(known, cap or 10 ** 9) if d.deal_id not in known]
            for d in deals:
                d.backfill = True
            if deals:
                yield deals
            return

        fetcher = self.make_fetcher()
        seen: set[str] = set()  # 同一実行内の重複ページ/重複案件の排除
        got = 0                 # 新規に取得できた（タイトル有り）件数
        empty_streak = 0
        for page in range(1, self.max_backfill_pages + 1):
            url = self.page_url(page)
            if not url:
                break
            resp = fetcher.get(url)
            items = self.parse_list(resp)
            fresh = [d for d in items if d.deal_id and d.deal_id not in seen]
            seen.update(d.deal_id for d in fresh)
            if not fresh:
                empty_streak += 1
                if empty_streak >= 2:  # 連続で空ページなら末尾に到達したとみなす
                    break
                continue
            empty_streak = 0
            batch = [d for d in fresh if d.deal_id not in known]  # 既取得は再upsert不要
            for d in batch:
                d.backfill = True
            if batch:
                got += sum(1 for d in batch if d.title)
                yield batch
            if cap and got >= cap:
                break

    def apply_seed_policy(self, deals: list[Deal], known: set[str], threshold: int = 200) -> list[Deal]:
        """全件掲載型（カタログ型）サイト向け: 未知IDが閾値を超える場合は初回実行とみなし
        全件をシード（表示対象外）として登録する"""
        unknown = sum(1 for d in deals if d.deal_id not in known)
        if unknown > threshold:
            for d in deals:
                d.seeded = True
        return deals


_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapDiffAdapter(SiteAdapter):
    """sitemapのURL一覧と既知IDの差分から新着を検知する方式の共通実装。

    初回実行（既知IDが空に近い）ではsitemap全件が「新着」に見えてしまうため、
    未知IDが seed_threshold を超える場合は詳細ページを取得せずIDのみ登録する。
    """
    sitemap_url: str = ""
    seed_threshold: int = 200
    max_detail_fetch: int = 30  # 1回の実行で詳細ページを取りに行く上限（負荷抑制）

    def deal_id_from_url(self, url: str) -> str | None:
        """sitemap内のURLから案件IDを抽出する（案件以外のURLはNoneを返す）"""
        raise NotImplementedError

    def fetch_detail(self, fetcher: PoliteFetcher, deal_id: str, url: str) -> Deal | None:
        """案件詳細ページからタイトル・ポイントを取得する"""
        raise NotImplementedError

    def _load_sitemap_entries(self, fetcher: PoliteFetcher) -> list[tuple[str, str]]:
        """(url, lastmod) のリストを返す。gzipにも対応"""
        resp = fetcher.get(self.sitemap_url)
        content = resp.content
        if self.sitemap_url.endswith(".gz") or content[:2] == b"\x1f\x8b":
            content = gzip.GzipFile(fileobj=io.BytesIO(content)).read()
        root = ElementTree.fromstring(content)
        entries = []
        for node in root.findall("sm:url", _SITEMAP_NS):
            loc = node.findtext("sm:loc", default="", namespaces=_SITEMAP_NS).strip()
            lastmod = node.findtext("sm:lastmod", default="", namespaces=_SITEMAP_NS).strip()
            if loc:
                entries.append((loc, lastmod))
        return entries

    def fetch_deals(self, known: set[str], max_items: int) -> list[Deal]:
        fetcher = self.make_fetcher()
        entries = self._load_sitemap_entries(fetcher)
        unknown = []
        for url, lastmod in entries:
            deal_id = self.deal_id_from_url(url)
            if deal_id and deal_id not in known:
                unknown.append((deal_id, url, lastmod))

        # 初回実行とみなす場合はIDのみ登録（詳細ページへの大量アクセスを避ける）
        if len(unknown) > self.seed_threshold:
            return [
                Deal(site=self.key, deal_id=i, title="", points_text="",
                     yen=None, percent=None, url=u, seeded=True)
                for i, u, _ in unknown
            ]

        unknown.sort(key=lambda e: e[2], reverse=True)  # lastmodが新しい順に優先
        deals = []
        for deal_id, url, _ in unknown[: min(self.max_detail_fetch, max_items)]:
            try:
                deal = self.fetch_detail(fetcher, deal_id, url)
            except Exception:
                continue  # 個別ページの失敗は全体を止めない
            if deal:
                deals.append(deal)
        return deals

    def backfill_deals(self, known: set[str], cap: int):
        """sitemap掲載の全案件の詳細を取得する（既取得IDはスキップ）。

        1件ずつ yield するため、途中で中断してもその時点までが保存される。
        """
        fetcher = self.make_fetcher()
        entries = self._load_sitemap_entries(fetcher)
        got = 0
        for url, lastmod in entries:
            deal_id = self.deal_id_from_url(url)
            if not deal_id or deal_id in known:
                continue
            if cap and got >= cap:
                break
            try:
                deal = self.fetch_detail(fetcher, deal_id, url)
            except Exception:
                continue
            if deal and deal.title:
                deal.backfill = True
                got += 1
                yield [deal]


def absolute_url(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return base.rstrip("/") + "/" + href.lstrip("/")


def extract_first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None
