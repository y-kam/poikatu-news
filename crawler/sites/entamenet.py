"""えんためねっと — 円貯め一覧 /savings.asp（SSR・Shift_JIS）から取得。

新着順に並んだ1ページ目をポーリングする方式（新着が先頭に来るためシード不要）。
還元額は「6,732円」のように円そのもの表記なのでrate=1.0で円換算される。
ポイントUP案件は <s>旧額</s> → 新額 の形で併記されるため、旧額の<s>を
除去してから現在額（新額）を取得する。

2ページ目以降はページ送りフォームのPOSTのみ（GETクエリ ?pag=N は無視される実測。
2026-07-12に POST pag=N で正しく別ページが返ることを確認）。全件バックフィルは
このPOSTページングで全ページ（約123頁）を巡回する。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

LIST_URL = "https://www.yentame.net/savings.asp"
BASE = "https://www.yentame.net"

# fb-likeのdata-href（detailad.asp?aid=...）から案件IDを取り出す
_AID_RE = re.compile(r"aid=([^&#]+)")


@register
class EntamenetAdapter(SiteAdapter):
    key = "entamenet"
    name = "えんためねっと"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        resp = fetcher.get(LIST_URL)
        resp.encoding = "cp932"  # charset宣言はShift_JISだがrequestsが誤判定するため明示
        return self._parse_list(resp.text, max_items)

    def _parse_list(self, html: str, max_items: int) -> list:
        """一覧ページ（1ページ目・POSTページング共通）から案件を抽出する。"""
        soup = BeautifulSoup(html, "lxml")
        deals = {}
        for post in soup.select("div.post")[:max_items]:
            # 案件IDと詳細URLは fb-like の data-href（detailad.asp?aid=...）から取る
            # （タイトルリンクは javascript:advdetail1('aid') 形式でURLになっていない）
            fb = post.select_one("div.fb-like")
            href = fb.get("data-href", "") if fb else ""
            m = _AID_RE.search(href)
            if not m:
                continue
            deal_id = m.group(1)
            if deal_id in deals:  # 一覧内の重複掲載をIDで排除
                continue
            title = post.select_one("div.title h3 a")
            money = post.select_one("li.money")
            if not (title and money):
                continue
            # ポイントUP案件は <s>旧額</s> を除去して現在額（新額）だけ残す
            for old in money.select("s"):
                old.decompose()
            strong = money.select_one("strong")
            # 旧額除去後に「→ 新額」の矢印が先頭に残るため取り除く
            points_text = (strong or money).get_text(strip=True).lstrip("→ 　")
            condition = post.select_one("li.condition")
            url = href if href.startswith("http") else BASE + "/" + href.lstrip("/")
            deal = self.make_deal(
                deal_id,
                title.get_text(strip=True),
                points_text,
                url,
                condition.get_text(strip=True) if condition else "",
            )
            # 増額中のUPアイコン（i_up.gif。<s>旧額</s>→新額の隣）→再新着判定
            deals[deal_id] = self.flag_site_new(deal, str(post))
        return list(deals.values())

    # --- 全件バックフィル用: ページ送りフォームと同じ POST pag=N で全ページを巡回 --------
    # GETクエリはサーバに無視されるため、公開ページのフォーム送信をそのまま再現する。
    # 運用ポリシー（未ログイン・GETのみ）の唯一の例外で、手動の --backfill 実行時にのみ使う。
    # 巡回の挙動は base.backfill_deals と同じ（重複排除・連続2空ページで打ち切り・逐次yield）。
    def backfill_deals(self, known, cap):
        fetcher = self.make_fetcher()
        seen: set[str] = set()
        got = 0
        empty_streak = 0
        for page in range(1, self.max_backfill_pages + 1):
            if page == 1:
                resp = fetcher.get(LIST_URL)
            else:
                resp = fetcher.post(LIST_URL, data={"pag": str(page)})
            resp.encoding = "cp932"
            fresh = [d for d in self._parse_list(resp.text, 10 ** 9) if d.deal_id not in seen]
            seen.update(d.deal_id for d in fresh)
            if not fresh:
                empty_streak += 1
                if empty_streak >= 2:  # 連続で空ページなら末尾に到達したとみなす
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
