"""ポインティア（Pointier） — 申込系・買い物系の各1ページ目（SSR・EUC-JP）から取得。

一覧は新着（案件ID）降順で、先頭が最新。charset宣言はEUC-JPだがヘッダに無いため
明示デコードする。申込系(cat=1_)と買い物系(cat=2_)を各p=1で取得して統合し、
案件ID降順（＝全体で新着順）に並べて返す新着ポーリング方式（シード不要）。

2ページ目以降は p=N（1始まり・20件/頁）で取得できる（2026-09-09確認）。全件バックフィルは
両カテゴリの全ページを巡回する（page_url は単一系列しか表せないため backfill_deals を
自前実装する）。
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://pointier.net/"
# 一覧URL。cat=カテゴリ / p=ページ番号（1始まり）。新着降順で先頭が最新
LIST_URL = "https://pointier.net/?cat={cat}&p={page}"
CATEGORIES = ("1_", "2_")  # 1_=お申し込みでためる / 2_=お買い物でためる
# 詳細ページURL（./open/16740.html / open\16740.html 混在）から案件IDを抽出
_ID_RE = re.compile(r"open/(\d+)\.html")


@register
class PointierAdapter(SiteAdapter):
    key = "pointier"
    name = "ポインティア"

    def parse_list(self, resp):
        resp.encoding = "euc-jp"  # ヘッダにcharset宣言が無くmetaはEUC-JP
        soup = BeautifulSoup(resp.text, "lxml")
        deals = []
        for item in soup.select("div#result ul#default_view > li"):
            link = item.select_one("div.title a[href]")
            rate = item.select_one("div.point span.rate")
            if not (link and rate):
                continue
            href = link.get("href", "").replace("\\", "/")  # \区切り混在を正規化
            m = _ID_RE.search(href)
            if not m:
                continue
            # 獲得条件（申し込み/商品購入/旅行完了 等）はr1内のclass無しdivに入る
            cond_div = item.select_one("div.r1 > div:not([class])")
            deals.append(self.make_deal(
                m.group(1),
                link.get_text(strip=True),
                rate.get_text(strip=True),  # "800Ｐ" / "5.5％"（%はparse_pointsが判別）
                urljoin(BASE, href),
                cond_div.get_text(strip=True) if cond_div else "",
            ))
        return deals

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        deals: dict = {}
        for cat in CATEGORIES:
            for deal in self.parse_list(fetcher.get(LIST_URL.format(cat=cat, page=1))):
                deals.setdefault(deal.deal_id, deal)  # カテゴリ間の重複掲載はIDで排除
        # 案件IDは発番順のため降順＝全体で新着順。統合後に並べ直す
        ordered = sorted(deals.values(), key=lambda d: int(d.deal_id), reverse=True)
        return ordered[:max_items]

    # --- 全件バックフィル用: カテゴリごとに全ページを巡回する ---------------------------
    # 巡回の挙動は base.backfill_deals と同じ（実行内の重複排除・連続2空ページでそのカテゴリを
    # 打ち切り・cap 到達で終了・バッチ単位で逐次 yield）。
    def backfill_deals(self, known, cap):
        fetcher = self.make_fetcher()
        seen: set[str] = set()  # カテゴリ間・ページ間の重複案件を排除
        got = 0
        for cat in CATEGORIES:
            empty_streak = 0
            for page in range(1, self.max_backfill_pages + 1):
                items = self.parse_list(fetcher.get(LIST_URL.format(cat=cat, page=page)))
                fresh = [d for d in items if d.deal_id not in seen]
                seen.update(d.deal_id for d in fresh)
                if not fresh:
                    empty_streak += 1
                    if empty_streak >= 2:  # 連続で空（または既出のみ）＝このカテゴリの末尾
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
