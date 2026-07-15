"""クロールの取得実績（data/crawl_metrics.json）を評価し、サイト仕様変更による
パーサ破損（取得0件・激減・解析不能率スパイク・例外）を検知するCI用ゲート。

致命的（critical＝2回以上連続の異常）があれば終了コード1で返し、GitHub Actions の
ジョブを失敗させて、失敗通知メールで迅速に気づけるようにする。デプロイ後の最終ステップで
実行する想定（このスクリプトは記録を読むだけ。サイト公開やデータ更新は妨げない）。

使い方:
  python check_health.py            # 評価してサマリ表示。critical があれば exit 1
  python check_health.py --strict   # warning（1回だけの異常）も失敗扱い（exit 1）にする

判定基準・しきい値は crawler/health.py に集約（run.py の実行時サマリと同一ロジック）。
"""
import argparse
import sys

from crawler import health

# Windowsコンソール（cp932）での日本語出力の文字化けを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="warning（1回だけの異常）も失敗扱いにする")
    args = parser.parse_args()

    metrics = health.load()
    anomalies = health.evaluate(metrics)
    criticals = health.report(anomalies)
    if args.strict and anomalies:
        return 1
    return 1 if criticals else 0


if __name__ == "__main__":
    sys.exit(main())
