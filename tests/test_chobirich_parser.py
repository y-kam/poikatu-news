"""ちょびリッチの一覧パーサが、2026-11 リニューアル後のHTML構造から案件を取れることを検証する。

HTMLは実ページ（/search?category=earn&feature=new・/smartphone/、2026-11-18取得）の構造を
必要な部分だけ残して縮めたもの。リニューアルで黙って0件になった前例があるため、
案件の見分け方（リンク内の見出し・ポイント欄）と除外すべき要素を固定しておく。
"""
import unittest

from crawler.sites.chobirich import ChobirichAdapter

CONFIG = {"rate": 0.5, "list_new_markers": [">新規掲載<"]}

PC_HTML = """
<div class="box-border grid grid-cols-2">
  <div class="box-border relative bg-white rounded-lg">
    <a class="relative flex flex-col h-full p-3.5" href="/ad_details/1890427">
      <div class="absolute -top-2.5 -left-1.5 flex gap-1"><span class="inline-block rounded-full bg-attention text-white">新規掲載</span> </div>
      <div class="w-full"><img alt="ジェイエステティック" src="x.gif"/></div>
      <div class="flex-1"><div class="flex-1 min-w-0 gap-1">
        <h3 class="text-lg"><span class="font-bold">ジェイエステティック</span></h3>
        <p class="grid-col-1 text-sm"> <span class="line-clamp-1">330円以上のコース契約完了</span> </p>
      </div>
      <div class="text-right"> <span class="flex font-bold text-attention [&amp;_s]:block">2,000pt</span></div></div>
    </a>
  </div>
  <div class="box-border relative bg-white rounded-lg">
    <a class="relative flex flex-col h-full p-3.5" href="/ad_details/1902801">
      <div class="flex-1"><h3 class="text-lg"><span>着るだけリカバリーウェア</span></h3>
      <div class="text-right"> <span class="flex font-bold text-attention">8％</span></div></div>
    </a>
  </div>
  <div class="box-border relative bg-white rounded-lg">
    <a class="relative flex flex-col h-full p-3.5" href="/ad_details/redirect/1700001">
      <div class="flex-1"><h3 class="text-lg"><span>ポイントUP案件</span></h3>
      <div class="text-right"> <span class="flex font-bold text-attention"><s>7,500pt</s>13,500pt</span></div></div>
    </a>
  </div>
</div>
<ul class="mb-5">
  <li class="mb-2"><a href="/ad_details/member_only/1901795">ちょびビンゴ</a></li>
</ul>
"""

APP_HTML = """
<ul class="grid grid-cols-2 gap-2.5">
  <li class="relative"><a href="/ad_details/redirect/1819899" target="_blank"><img alt="skyfall_オファーウォール_誘導" src="b.png"/></a></li>
</ul>
<ul>
  <li class="relative border-b border-dotted">
    <div class="mb-1 flex"><a href="https://www.chobirich.com/mypage/rank#rank_tokuten"><div>＃ランクアップ対象</div></a></div>
    <a class="relative grid" href="/ad_details/1903630">
      <div class="self-start"><img src="icon.png"/></div>
      <div><h2 class="mb-1 font-bold">アツアツ冒険伝</h2>
        <p class="mb-1 text-headline font-bold text-attention">最大2,801pt</p>
        <p class="mb-1 line-clamp-3 text-sub">新規アプリインストール後、各ステップクリア</p></div>
    </a>
  </li>
</ul>
"""


class ChobirichPcListTest(unittest.TestCase):
    def setUp(self):
        self.deals = {d.deal_id: d for d in ChobirichAdapter(CONFIG).parse_pc_list(PC_HTML)}

    def test_extracts_cards_and_skips_sidebar_links(self):
        """案件カードだけを取り、サイドバーの会員向けコンテンツ（member_only）は拾わない"""
        self.assertEqual(sorted(self.deals), ["1700001", "1890427", "1902801"])

    def test_title_points_and_canonical_url(self):
        """案件名・ポイント・円換算を取り、URLは保存済みデータと同じ末尾スラッシュ付きにそろえる"""
        d = self.deals["1890427"]
        self.assertEqual(d.title, "ジェイエステティック")
        self.assertEqual(d.points_text, "2,000pt")
        self.assertEqual(d.yen, 1000.0)
        self.assertEqual(d.url, "https://www.chobirich.com/ad_details/1890427/")
        self.assertEqual(self.deals["1700001"].url, "https://www.chobirich.com/ad_details/1700001/")
        self.assertEqual(self.deals["1902801"].percent, 8.0)

    def test_strikethrough_old_points_are_removed(self):
        """ポイントUP案件の旧値（<s>）は除き、現在値で円換算する"""
        d = self.deals["1700001"]
        self.assertEqual(d.points_text, "13,500pt")
        self.assertEqual(d.yen, 6750.0)

    def test_new_badge_sets_site_new(self):
        """「新規掲載」バッジ付きのカードだけ site_new=True（再新着判定の経路③）"""
        self.assertTrue(self.deals["1890427"].site_new)
        self.assertFalse(self.deals["1902801"].site_new)


class ChobirichAppListTest(unittest.TestCase):
    def test_extracts_app_items_with_condition(self):
        """アプリ一覧は見出し付きの案件だけを取り、オファーウォールのバナーは拾わない"""
        deals = ChobirichAdapter(CONFIG).parse_app_list(APP_HTML)

        self.assertEqual([d.deal_id for d in deals], ["1903630"])
        d = deals[0]
        self.assertEqual(d.title, "アツアツ冒険伝")
        self.assertEqual(d.points_text, "最大2,801pt")
        self.assertEqual(d.condition, "新規アプリインストール後、各ステップクリア")
        self.assertEqual(d.url, "https://www.chobirich.com/ad_details/1903630/")


if __name__ == "__main__":
    unittest.main()
