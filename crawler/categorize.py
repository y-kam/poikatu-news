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
import re
import unicodedata
from collections import Counter
from pathlib import Path

from crawler.normalize import normalize_title

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "categories.json"
CORP_FILE = CONFIG_DIR / "corporate.json"


def load_categories() -> list[dict]:
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_pattern_set(filename: str) -> dict:
    """案件名＋獲得条件への部分一致で横断フラグ（法人・投資・入金・年収など）を立てる
    パターン設定（config/<filename>）を読む。パターンは分類・法人判定と同じ NFKC＋
    小文字化で正規化して返すため、config 側は全角/半角・大小文字を気にせず記述できる。"""
    with (CONFIG_DIR / filename).open(encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["patterns"] = [_normalize(p) for p in cfg["patterns"]]
    return cfg


def load_corporate() -> dict:
    """法人・ビジネス向け案件の検知設定を読む（load_pattern_set の別名。既存の
    呼び出し・監査スクリプトとの互換のため残す）。"""
    return load_pattern_set("corporate.json")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def matches_patterns(deal: dict, cfg: dict) -> bool:
    """案件名＋獲得条件に cfg（load_pattern_set の戻り値）のパターンが1つでも含まれるか。
    カテゴリ（category）とは独立した横断フラグ判定で、表示時に毎回行う（パターンを
    更新すれば再クロールなしで反映される）。"""
    text = _normalize(deal.get("title", "") + " " + (deal.get("condition") or ""))
    return any(p in text for p in cfg["patterns"])


def is_corporate(deal: dict, corp: dict) -> bool:
    """個人（一般消費者）では申込めない法人・事業者向け案件か（matches_patterns の別名。
    既存の呼び出し・監査スクリプトとの互換のため残す）。"""
    return matches_patterns(deal, corp)


# しきい値フィルタ用の金額抽出。表示時に案件名＋獲得条件から「投資・入金で用意が必要な額」
# と「年収条件で必要な年収」を近似的に取り出す。抽出できない案件はしきい値では絞らない
# （＝隠さない）側に倒し、丸ごと除外トグルに委ねる。
# 「万円」の円は任意（実データには「50万以上」「年収500万以上」のように円を省く表記がある）。
# ただし「50万ポイント／50万人」等の報酬・件数語を前金額と誤認しないよう、円が無い場合は
# 直後が金額文脈（以上/以内/未満/分/を/の/＋/、。/空白/末尾）のときだけ採る。
_MAN_YEN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*万(?:円|(?=以上|以内|未満|分|を|の|、|。|\+|＋|\s|$))")
_YEN = re.compile(r"(\d[\d,]*)\s*円")                  # 「5000円」→ 5000（円単位の数値）
_INCOME_MAN = re.compile(r"年収\s*(\d[\d,]*(?:\.\d+)?)\s*万円?")  # 「年収700万円」「年収1,000万円」→ 700/1000


def required_yen(deal: dict) -> int | None:
    """投資・入金で用意が必要な金額（円）を近似する。年収表記（所得条件で前金ではない）を
    除いた上で、最大の『○万円』を採る（無ければ最大の『○円』）。前金は通常 万円単位で最大額
    として現れ、報酬（獲得額）は円・ポイントで別記されるため、この採り方で用意額を近似できる。
    抽出できなければ None（しきい値では隠さない）。"""
    text = unicodedata.normalize("NFKC", deal.get("title", "") + " " + (deal.get("condition") or ""))
    text = _INCOME_MAN.sub("", text)  # 「年収700万円」は用意額でないため対象から除く
    mans = _MAN_YEN.findall(text)
    if mans:
        return int(max(float(m.replace(",", "")) for m in mans) * 10000)
    yens = [int(y.replace(",", "")) for y in _YEN.findall(text)]
    return max(yens) if yens else None


def required_income_man(deal: dict) -> float | None:
    """年収条件で要求される年収（万円）を取り出す。『年収○万円』の最大値。無ければ None。"""
    text = unicodedata.normalize("NFKC", deal.get("title", "") + " " + (deal.get("condition") or ""))
    vals = [float(m.replace(",", "")) for m in _INCOME_MAN.findall(text)]
    return max(vals) if vals else None


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
