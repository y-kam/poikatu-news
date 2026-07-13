"""ポイントランド — 新着順一覧（旧ASP・Shift_JIS）から取得。

`top.asp?c=0100&s=0` は「すべて表示」カテゴリの新着順一覧（c=0100=全カテゴリ横断,
s=0=新着順）。1ページ18件で先頭が最新のため、新着ポーリング型として1ページのみ取得する。
旧世代のテーブルレイアウトでCSSクラスがほぼ無いため、1案件=table[height="140"]を単位に
advviewアンカーとimg[alt]見出しを手がかりに抽出する。10ポイント=1円（rate=0.1）。
"""
import re

from bs4 import BeautifulSoup

from crawler.sites import register
from crawler.sites.base import SiteAdapter

BASE = "https://www.point-land.net"
LIST_URL = BASE + "/top.asp?c=0100&s=0"  # c=0100:すべて表示 / s=0:新着順

# advview('ID') の引数が案件ID。数字IDのほか j4174/s26928 等の英字プレフィックス付きもある
_ID_RE = re.compile(r"advview\('([^']+)'\)")


@register
class PointLandAdapter(SiteAdapter):
    key = "point_land"
    name = "ポイントランド"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        resp = fetcher.get(LIST_URL)
        resp.encoding = "cp932"  # ヘッダにcharset宣言が無くShift_JIS（cp932）
        soup = BeautifulSoup(resp.text, "lxml")

        deals = []
        # 1案件カード = 高さ140のテーブル（旧レイアウトのためclassが無くheight属性で特定）
        for card in soup.select('table[height="140"]')[:max_items]:
            deal_id, title = self._extract_id_title(card)
            if not deal_id:
                continue
            points_text = self._text_after_icon(card, "ポイント")
            if not points_text:
                continue
            condition = self._text_after_icon(card, "条件")
            deals.append(self.make_deal(
                deal_id,
                title,
                points_text,
                f"{BASE}/cert.asp?adv={deal_id}",  # 案件詳細ページ
                condition,
            ))
        return deals

    def _extract_id_title(self, card):
        """カード内のadvviewアンカーから案件IDと案件名を得る。

        同一IDのアンカーが画像用・テキスト用など複数並ぶため、
        テキストを持つアンカーを案件名として採用する。
        """
        for a in card.find_all("a", onclick=_ID_RE):
            m = _ID_RE.search(a.get("onclick", ""))
            title = a.get_text(strip=True)
            if m and title:
                return m.group(1), title
        return None, ""

    def _text_after_icon(self, card, alt):
        """img[alt=<alt>]（ポイント/条件アイコン）の隣のtdのテキストを返す。"""
        icon = card.find("img", alt=alt)
        if not icon:
            return ""
        td = icon.find_parent("td")
        sibling = td.find_next_sibling("td") if td else None
        return sibling.get_text(" ", strip=True) if sibling else ""
