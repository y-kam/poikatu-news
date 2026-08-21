"""ポイントミュージアム — 一覧ページ（SSR・Shift_JIS）の先頭ページから取得。

sitemap（sitemap-list.xml）は更新が止まっており（lastmod最新2025-04-13）、実際の掲載
案件より約500件少ない。sitemap差分方式では新着を1件も検知できなくなっていたため
（2026-08-21調査: 一覧1頁目20件中14件がsitemap未収録）、一覧ページのID差分に切り替えた。

一覧の既定並び（st=空）は新しい案件が前方に集まる（実測: 1頁目14/20・2頁目9/20が
sitemap未収録、38頁目・76頁目は0件）。日次は各カテゴリ先頭 DAILY_PAGES ページを見る。
一覧HTMLにタイトル・ポイント・獲得条件が揃っているため詳細ページの取得は不要。

対象カテゴリはサービス全件（cat=0000・約76頁）と買い物（cat=0800・約27頁）の2系列。
案件IDは onclick="advview('<id>')" から取り、詳細URLは従来と同じ ct.asp?adv=<id>
（既存データ・リンク死活チェックと互換）。レスポンス・metaともcharset宣言が無い
Shift_JISのため cp932 を明示する。10pt=1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://www.point-museum.com/"
LIST_URL = BASE + "plist.asp?cat={cat}&st=&searchkey=&p={page}"
DETAIL_URL = BASE + "ct.asp?adv={deal_id}"
CATEGORIES = ("0000", "0800")  # サービス全件（約76頁）/ 買い物（約27頁）。20件/頁
DAILY_PAGES = 3                # 日次で見る各カテゴリの先頭ページ数（新着は前方に集まる）
# 未知IDがこの件数を超えた回は「新着の大量発生」ではなくカタログ同期（sitemap時代の
# 取りこぼし・仕様変更後の初回）とみなし、シード登録（表示対象外）にして新着セクションが
# 溢れるのを防ぐ。シードは次回以降のクロールで本文取得済みとして表示解禁される
# （store.upsert の seed_filled。新着扱いにはならない）
SEED_THRESHOLD = 30
# 一覧アイテムの onclick="advview('<id>');" から案件ID（英数字混在）を抽出する
_ID_RE = re.compile(r"advview\('([^']+)'\)")


@register
class PointMuseumAdapter(SiteAdapter):
    key = "point_museum"
    name = "ポイントミュージアム"

    def page_url(self, page):
        """バックフィルの単一系列用（未使用）。複数カテゴリを回るため backfill_deals を実装する。"""
        return None

    def parse_list(self, resp):
        """一覧レスポンスから案件を抽出する。タイトル・ポイント・条件は一覧に揃っている。"""
        resp.encoding = "cp932"  # charset宣言が無いShift_JISのため明示
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("#prarea div.bannerwide"):
            link = item.select_one(".bannertitle a")
            point = item.select_one(".text p.pt")
            if not (link and point):
                continue
            m = _ID_RE.search(link.get("onclick", "")) or _ID_RE.search(str(item))
            if not m:
                continue
            title = link.get_text(strip=True)
            points_text = point.get_text(" ", strip=True).replace("\xa0", " ")
            if not (title and points_text):
                continue
            joken = item.select_one(".text p.joken")
            deal = self.make_deal(
                m.group(1), title, points_text,
                DETAIL_URL.format(deal_id=m.group(1)),
                joken.get_text(" ", strip=True) if joken else "",
            )
            deals.append(self.flag_site_new(deal, str(item)))
        return deals

    def fetch_deals(self, known, max_items):
        """日次は各カテゴリの先頭 DAILY_PAGES ページを巡回してID差分に載せる。"""
        fetcher = self.make_fetcher()
        deals = []
        for cat in CATEGORIES:
            for page in range(1, DAILY_PAGES + 1):
                if len(deals) >= max_items:
                    break  # 上限到達後は残りカテゴリ・ページを取りに行かない
                deals += self.parse_list(fetcher.get(LIST_URL.format(cat=cat, page=page)))
        # カテゴリ間で同一IDが被っても upsert が (site, deal_id) で重複排除する
        return self.apply_seed_policy(deals[:max_items], known, SEED_THRESHOLD)

    # --- 全件バックフィル用: カテゴリごとに全ページを巡回する ---------------------------
    # page_url は単一のページ系列しか表せないため backfill_deals 自体を実装する。
    # 巡回の挙動は base.backfill_deals と同じ（実行内の重複排除・連続2空ページでそのカテゴリを
    # 打ち切り・cap 到達で終了・バッチ単位で逐次 yield）。
    def backfill_deals(self, known, cap):
        fetcher = self.make_fetcher()
        seen: set[str] = set()  # カテゴリ間の重複案件を排除（同一案件が両リストに載る）
        got = 0
        for cat in CATEGORIES:
            empty_streak = 0
            for page in range(1, self.max_backfill_pages + 1):
                resp = fetcher.get(LIST_URL.format(cat=cat, page=page))
                fresh = [d for d in self.parse_list(resp)
                         if d.deal_id and d.deal_id not in seen]
                seen.update(d.deal_id for d in fresh)
                if not fresh:
                    empty_streak += 1
                    if empty_streak >= 2:  # 連続で空＝最終ページ以降（ASPは末尾で同じ頁を返す）
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
