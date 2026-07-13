"""たまるモール — JSON API（カタログ型）から全件取得。

ふるなびが運営するポイントモール。案件一覧のJSON APIが公開されており、
1リクエストで全件（約396件）を取得できる。新着順ソートが無い全件掲載型のため、
Gポイント同様に全件を取得してID差分で新着検知し、初回はシード登録する。
1コイン=1円のためrate=1.0。BeautifulSoupは不要。
"""
from crawler.sites import register
from crawler.sites.base import SiteAdapter

# 全案件を一括で返すJSON API（1リクエストで全件取得できる）
LIST_URL = "https://furunavi.jp/tamaru/Advertisement/JsonAdvertisementList"
# 案件詳細ページ（AdvertisementIdをクエリに付与）
DETAIL_URL = "https://furunavi.jp/tamaru/Advertisement/Detail?AdvertisementId={}"


def _format_rate(rate: float) -> str:
    """料率（0.005=0.5%）を末尾ゼロを落とした%表記に整形する"""
    percent = rate * 100
    text = f"{percent:f}".rstrip("0").rstrip(".")
    return f"{text}%"


@register
class TamaruMallAdapter(SiteAdapter):
    key = "tamaru_mall"
    name = "たまるモール"

    def fetch_deals(self, known, max_items):
        fetcher = self.make_fetcher()
        resp = fetcher.get(LIST_URL)
        resp.encoding = "utf-8"  # charset宣言はutf-8だが取り違え防止で明示
        data = resp.json()

        deals = []
        for ad in data.get("AdvertisementList", []):
            deal_id = ad.get("AdvertisementId")
            title = (ad.get("Name") or "").strip()
            if deal_id is None or not title:
                continue

            reward_type = ad.get("RewardType")
            promoted = ad.get("PromotedNow")
            points_text = ""

            if reward_type == 1:
                # 固定コイン案件。ポイントUP中は昇格後の値（PromotionFixedReward）を採用
                value = ad.get("PromotionFixedReward") if promoted else None
                if value is None:
                    value = ad.get("FixedReward")
                if value:
                    # 1コイン=1円。整数コインなので整数表記でparse_pointsへ渡す
                    points_text = f"{int(value):,}コイン"
            elif reward_type == 2:
                # 料率案件。ポイントUP中は昇格後の料率（PromotionRateReward）を採用
                rate = ad.get("PromotionRateReward") if promoted else None
                if rate is None:
                    rate = ad.get("RateReward")
                if rate:
                    # %を残して%還元案件として扱わせる（円換算せずpercent側へ）
                    points_text = _format_rate(rate)

            if not points_text:
                continue  # 還元額が取れない案件はスキップ

            # ConversionAction は成果条件テキスト（例「新規会員登録」）
            condition = (ad.get("ConversionAction") or "").strip()
            deals.append(self.make_deal(
                str(deal_id),
                title,
                points_text,
                DETAIL_URL.format(deal_id),
                condition,
            ))

        # 全件カタログ型のため初回はシード登録して大量新着フラッシュを防ぐ
        return self.apply_seed_policy(deals, known)