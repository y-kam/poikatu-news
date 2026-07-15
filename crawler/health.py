"""クロールの取得実績を記録し、サイト仕様変更によるパーサ破損を検知する。

各サイトの fetch_deals が「毎回どれくらいの件数・どんな質のデータを返したか」を
data/crawl_metrics.json に実行ごとに蓄積し、直近1回を過去実績（同一サイトの中央値
ベースライン）と比較して、次の異常を検知する:
  - error        : クロール中の例外（サイト仕様変更でHTTP/パースが落ちた等）
  - zero         : 一覧取得型サイトで取得0件（通常は数十件返るサイトが0＝構造変化の疑い）
  - drop         : 取得件数がベースラインから激減
  - unparsable   : 取得はできたがポイント換算不能の割合が急増（ポイント抽出セレクタの破損）
  - empty_title  : タイトル取得できない割合が急増（タイトル抽出セレクタの破損）

一時的な障害（瞬断・空応答）での誤報を避けるため、リンク死活チェック（linkcheck）と
同じ「連続回数」方式を採る: 1回だけの異常は warning（報告のみ）、2回以上連続で同じ
異常が続けば critical（CI失敗＝通知メールで迅速に気づく）とする。

差分取得型（SitemapDiffAdapter）のサイトは新着が無い日に取得0件が正常なため、
件数ベースの zero/drop 判定は行わず（catalog=False）、error と抽出品質
（unparsable/empty_title）で見る。
"""
import json
import os
import statistics
from pathlib import Path

METRICS_FILE = Path(__file__).resolve().parent.parent / "data" / "crawl_metrics.json"

WINDOW = 24            # サイトごとに保持する直近実行回数（1日6回×約4日ぶん）
MIN_HISTORY = 3        # 件数・割合系の判定に必要な過去実行回数（これ未満は error のみ判定）
CRITICAL_STREAK = 2    # この回数以上連続で同じ異常が続けば critical（＝CI失敗で通知）

ZERO_FLOOR = 6         # 取得0件を異常とみなすベースライン下限（小規模サイトの誤検知回避）
DROP_FLOOR = 20        # 激減判定を有効にするベースライン下限
DROP_RATIO = 0.35      # 現在値がベースライン×この比率未満なら激減
MIN_SAMPLE = 8         # unparsable/empty_title 率を評価する最小取得件数（少数だと率が暴れるため）
UNPARSABLE_RATIO = 0.5 # 解析不能率がこの値以上、かつ
UNPARSABLE_DELTA = 0.3 # ベースライン率＋この値以上のとき spike とみなす
EMPTY_TITLE_RATIO = 0.4

KINDS = ("error", "zero", "drop", "unparsable", "empty_title")
KIND_LABEL = {
    "error": "クロール例外",
    "zero": "取得0件",
    "drop": "取得激減",
    "unparsable": "ポイント解析不能が急増",
    "empty_title": "タイトル取得不能が急増",
}
SOFT_KINDS = {"drop"}  # 連続しても warning 止まり（誤検知しやすい軟らかい信号はCIを止めない）


def site_stat(deals=None, error: str | None = None, catalog: bool = True) -> dict:
    """1サイト1実行分の記録を作る。deals は fetch_deals の戻り（Deal のリスト）。
    catalog=False は差分取得型（新着0件が正常なサイト）で件数系の判定を無効にする印。
    抽出品質（u/t）の母数 b は seeded（IDのみ登録・本文空が正常）を除いた件数。"""
    if error is not None:
        return {"f": 0, "b": 0, "u": 0, "t": 0, "err": error, "cat": catalog}
    deals = deals or []
    body = [d for d in deals if not getattr(d, "seeded", False)]
    unparsable = sum(1 for d in body if d.yen is None and d.percent is None)
    empty_title = sum(1 for d in body if not d.title)
    return {"f": len(deals), "b": len(body), "u": unparsable, "t": empty_title,
            "err": None, "cat": catalog}


def load() -> dict:
    if METRICS_FILE.exists():
        try:
            return json.loads(METRICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"sites": {}}
    return {"sites": {}}


def save(metrics: dict) -> None:
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = METRICS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(METRICS_FILE)  # 書き込み途中のクラッシュで既存データを壊さない原子的置換


def record(metrics: dict, run_stats: dict, at: str) -> None:
    """今回のクロールのサイト別実績を蓄積する（サイトごとに直近 WINDOW 件だけ残す）。"""
    sites = metrics.setdefault("sites", {})
    for key, stat in run_stats.items():
        lst = sites.setdefault(key, [])
        lst.append({"at": at, **stat})
        del lst[:-WINDOW]


def _baseline_fetched(prior: list) -> "float | None":
    """過去実行の取得件数の中央値（例外回は除く）。判定基準にできる履歴が無ければ None。"""
    vals = [e["f"] for e in prior if not e["err"]]
    return statistics.median(vals) if vals else None


def _baseline_unparsable_rate(prior: list) -> float:
    """過去実行の解析不能率の中央値（例外回・少数サンプル回は除く）。無ければ 0。"""
    rates = [e["u"] / e["b"] for e in prior if not e["err"] and e.get("b", 0) >= MIN_SAMPLE]
    return statistics.median(rates) if rates else 0.0


def _is_bad(entry: dict, kind: str, baseline: "float | None", base_urate: float) -> bool:
    """ある実行(entry)が、指定した種類(kind)の異常に該当するか。
    例外回は "error" のみ該当（0件でも zero 扱いにはしない＝例外と件数異常を混同しない）。"""
    if entry["err"]:
        return kind == "error"
    if kind == "error":
        return False
    f, b, u, t, cat = entry["f"], entry.get("b", 0), entry["u"], entry["t"], entry.get("cat", True)
    if kind == "zero":
        return cat and f == 0 and baseline is not None and baseline >= ZERO_FLOOR
    if kind == "drop":
        return (cat and f > 0 and baseline is not None and baseline >= DROP_FLOOR
                and f < baseline * DROP_RATIO)
    if kind == "unparsable":
        return b >= MIN_SAMPLE and u / b >= UNPARSABLE_RATIO and u / b >= base_urate + UNPARSABLE_DELTA
    if kind == "empty_title":
        return b >= MIN_SAMPLE and t / b >= EMPTY_TITLE_RATIO
    return False


def _detail(cur: dict, kind: str, baseline: "float | None") -> str:
    """異常の内訳を人間向けの短い文字列にする。"""
    f, b, u, t = cur["f"], cur.get("b", 0), cur["u"], cur["t"]
    if kind == "error":
        return f"例外 {cur['err']}"
    if kind == "zero":
        return f"取得0件（通常は中央値{baseline:.0f}件）"
    if kind == "drop":
        return f"取得{f}件（通常は中央値{baseline:.0f}件）"
    if kind == "unparsable":
        return f"ポイント解析不能 {u}/{b}件（{u / b:.0%}）"
    if kind == "empty_title":
        return f"タイトル空 {t}/{b}件（{t / b:.0%}）"
    return ""


def evaluate(metrics: dict) -> list[dict]:
    """蓄積した実績から、直近1回を過去のベースラインと比較して異常を返す。
    末尾から同じ異常が続く回数(streak)を数え、CRITICAL_STREAK 以上なら critical
    （SOFT_KINDS は連続でも warning 止まり）。critical→site 名順に並べて返す。"""
    anomalies = []
    for site, entries in metrics.get("sites", {}).items():
        if not entries:
            continue
        cur, prior = entries[-1], entries[:-1]
        baseline = _baseline_fetched(prior)
        base_urate = _baseline_unparsable_rate(prior)
        enough = len(prior) >= MIN_HISTORY
        for kind in KINDS:
            if kind != "error" and not enough:
                continue  # ベースライン不足時は例外以外を判定しない（導入直後の誤報を防ぐ）
            if not _is_bad(cur, kind, baseline, base_urate):
                continue
            streak = 0
            for e in reversed(entries):  # 末尾から同じ異常が続く回数
                if _is_bad(e, kind, baseline, base_urate):
                    streak += 1
                else:
                    break
            critical = streak >= CRITICAL_STREAK and kind not in SOFT_KINDS
            anomalies.append({
                "site": site,
                "kind": kind,
                "severity": "critical" if critical else "warning",
                "streak": streak,
                "detail": _detail(cur, kind, baseline),
            })
    anomalies.sort(key=lambda a: (a["severity"] != "critical", a["site"], a["kind"]))
    return anomalies


def format_lines(anomalies: list[dict]) -> list[str]:
    # 装飾記号は使わない（一部コンソールの文字コードで表示できず落ちるのを避ける）。
    lines = []
    for a in anomalies:
        streak = f"（{a['streak']}回連続）" if a["streak"] > 1 else ""
        lines.append(f"[{a['severity']}] {a['site']}: {KIND_LABEL[a['kind']]}"
                     f" - {a['detail']}{streak}")
    return lines


def report(anomalies: list[dict], github: "bool | None" = None) -> int:
    """異常を標準出力（＋GitHub Actions のアノテーション）に出し、critical 件数を返す。
    github=None のときは環境変数 GITHUB_ACTIONS の有無で自動判定する。"""
    if github is None:
        github = bool(os.environ.get("GITHUB_ACTIONS"))
    if not anomalies:
        print("[health] パーサ破損の疑いなし（全サイト正常範囲）")
        return 0
    criticals = sum(1 for a in anomalies if a["severity"] == "critical")
    print(f"[health] 異常検知 {len(anomalies)}件（critical {criticals}件 / warning "
          f"{len(anomalies) - criticals}件）:")
    for a, line in zip(anomalies, format_lines(anomalies)):
        print("  " + line)
        if github:
            level = "error" if a["severity"] == "critical" else "warning"
            print(f"::{level} title=crawl-health {a['site']}::"
                  f"{a['site']}: {KIND_LABEL[a['kind']]} — {a['detail']}")
    return criticals
