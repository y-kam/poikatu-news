"""案件のカテゴリ分類。

config/categories.json のルールで、案件名＋獲得条件のテキストにキーワードが
含まれるかで判定する。表示時に毎回分類するため、ルールを更新すれば過去に
収集済みの案件にも再クロールなしで反映される。判定は2段階:
  1. 全カテゴリの priority_patterns を上から順に見て、最初に一致したキーへ
  2. 一致が無ければ全カテゴリの patterns を上から順に見て、最初に一致したキーへ

priority_patterns（最優先層）には「その語が出れば分類がほぼ確実」で他カテゴリの
一般語と衝突しないシグナルだけを置く。例:
  - app: 「[ios」「and_」等のOS表記。獲得条件文の「課金」「問い合わせ」等の他
    カテゴリ一般語より先に確定させるため。
  - shopping: 「商品購入」「100円ごと」「買い物」「楽天市場」等の明確なEC購入表記。
    これらが無いと通常層の一般語「購入」(paid) に先に吸われてしまう。
「ゲーム」「クリア」「通販」等の曖昧語は他カテゴリと衝突する（例:「クリアル」＝
金融相談、「ゲーム買取」＝買取査定、「○○通販・買取」＝買取）ため priority_patterns
には入れず、条件文の意図を表す語（買取・面談・定期購入 等）より後の通常層に置く。

通常層（patterns）の並び順の原則: 「条件文が示す取引意図」＞「名称に含まれる業態語」。
上から creditcard→account→paid(有料/初回/課金)→consult→app→infra→subscription(月額)→
freereg→shopping→paid(汎用)→ジャンル層(beauty/life/learning/travel) の順。
ジャンル層は取引意図でなく業態語（脱毛・クリーニング・英会話・ツアー等）で判定する
ため最後に置き、意図系カテゴリで拾えなかった「タイトルだけの案件」（獲得条件が
一覧ページから取れないサイト由来）の受け皿にする。汎用paidより前に置くと
「脱毛サロン来店」等の意図が読める案件まで吸ってしまうため順序を崩さないこと。
それでも残る「その他」は propagate_categories（名寄せ伝播）で他サイトの分類結果を
流用して救済する。subscription（月額サービス）は「月額・サブスク・定期
コース・放題・定額」等の継続課金シグナルで判定するが、SIM・光回線・ウォーターサーバーは
「定額・使い放題」を含んでも本質はインフラのため、infra の後に置き先にinfraで確定させる。
account に「入金」「株式」を単独では置かない（「入金確認」は購入完了
条件・「株式会社」は社名で頻出し金融以外を誤って金融に吸うため）。「入金」だけの取引は
末尾の汎用 paid で拾い、金融は 口座開設・証券・外為・binance 等の固有語で判定する。
"""
import json
import unicodedata
from collections import Counter
from pathlib import Path

from crawler.normalize import normalize_title

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "categories.json"
CORP_FILE = Path(__file__).resolve().parent.parent / "config" / "corporate.json"


def load_categories() -> list[dict]:
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_corporate() -> dict:
    """法人・ビジネス向け案件の検知設定を読む。パターンは分類と同じ NFKC＋小文字化で
    正規化して返すため、config 側は全角/半角・大小文字を気にせず記述できる。"""
    with CORP_FILE.open(encoding="utf-8") as f:
        corp = json.load(f)
    corp["patterns"] = [_normalize(p) for p in corp["patterns"]]
    return corp


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def is_corporate(deal: dict, corp: dict) -> bool:
    """個人（一般消費者）では申込めない法人・事業者向け案件か。案件名＋獲得条件に
    corporate.json のパターンが1つでも含まれれば真。カテゴリ（category）とは独立した
    横断フラグで、表示時に毎回判定する（パターンを更新すれば再クロールなしで反映される）。"""
    text = _normalize(deal.get("title", "") + " " + (deal.get("condition") or ""))
    return any(p in text for p in corp["patterns"])


def classify(deal: dict, categories: list[dict]) -> str:
    """案件のカテゴリキーを返す。どのルールにも該当しない%還元案件はショッピング扱い"""
    text = _normalize(deal.get("title", "") + " " + (deal.get("condition") or ""))
    for category in categories:
        if any(pattern in text for pattern in category.get("priority_patterns", ())):
            return category["key"]
    for category in categories:
        if any(pattern in text for pattern in category["patterns"]):
            return category["key"]
    if deal.get("percent") is not None:
        return "shopping"
    return "other"


def propagate_categories(deals: list[dict], categories: list[dict]) -> None:
    """「その他」になった案件へ、同一案件（名寄せキー一致）の他掲載で確定した
    カテゴリを多数決で流用する（各案件の category を直接上書き）。

    獲得条件を一覧ページから取得できないサイトはタイトルだけが判定材料になり、
    ゲーム名・店名のみの案件が「その他」に溜まる。同じ案件を条件付き・注記付きで
    掲載する他サイトの分類結果（例:「【GFRewards】○○（StepUp）」→アプリ）を
    名寄せキー（normalize_title: 括弧注記・記号を除去）経由で流用することで、
    キーワード追加なしに救済できる。票が同数の場合は categories.json の定義順
    （上ほど優先）で決定的に選ぶ。"""
    votes: dict[str, Counter] = {}
    for deal in deals:
        if deal["category"] != "other":
            key = normalize_title(deal["title"])
            if key:  # 空キー（タイトル欠損など）同士の誤マッチを防ぐ
                votes.setdefault(key, Counter())[deal["category"]] += 1
    priority = {c["key"]: i for i, c in enumerate(categories)}
    for deal in deals:
        if deal["category"] != "other":
            continue
        counter = votes.get(normalize_title(deal["title"]))
        if counter:
            deal["category"] = min(
                counter, key=lambda k: (-counter[k], priority.get(k, len(priority)))
            )
