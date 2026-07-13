"""案件データの永続化（data/deals.json）。

GitHub Actions 実行後にコミットされることで、実行間の差分検知（既知ID管理）が成立する。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "deals.json"

# 値動き履歴（変化点ログ）。キーは "site:deal_id"、値は [日付, 円換算, %還元] の
# リスト（古い順）。報酬が変化した案件だけ記録し、UPランキング・値動き履歴ページの
# データ源にする（renewed_at/renewed_from は減額で消える揮発データのため別持ちする）
HISTORY_FILE = DATA_FILE.parent / "history.json"

# 表示・リンクチェックの対象母集団を「初出からこの日数以内」に制限する。
# None なら全期間（掲載中の案件は日数によらず表示し、掲載終了はリンクチェックで自動除外）。
# 将来コスト（外部アクセス数・deals.json容量）を抑えたくなったら日数を設定する。
RECENT_DAYS = None

# 「新着」とみなす初出/再浮上からの日数（ローリング）。サイト生成（builder/generate.py）の
# 新着判定と、upsert の一覧バッジ再新着ルール（表示が切れてから再付与）の両方で使う。
NEW_DAYS = 3


def load() -> dict:
    if DATA_FILE.exists():
        with DATA_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {"deals": {}}


def save(store: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    tmp.replace(DATA_FILE)  # 書き込み途中のクラッシュで既存データを壊さないための原子的置換


def load_history() -> dict:
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history: dict) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(HISTORY_FILE)  # deals.json と同様の原子的置換


def record_history(history: dict, key: str, existing: dict, d, today: str) -> None:
    """既知案件の報酬が変わっていたら変化点を履歴に追記する。
    初めて変化を観測した案件は、旧値も「最後に観測した日」で遡って記録する
    （変化の無い案件は一切記録せず、履歴ファイルの肥大化を防ぐ）。
    同日内の再変化は最後の値で上書きし、1日複数回のクロールで変化点が細切れに
    増えないようにする（同日内に元の値へ戻ったら変化なしとして取り消す）。
    表示され得ない案件（seeded等）は記録しない（履歴ファイルの肥大化を防ぐ）。"""
    if _reward_change(existing, d) == 0 or not is_visible(existing):
        return
    entries = history.setdefault(key, [])
    if not entries:
        entries.append([existing.get("last_seen") or existing.get("first_seen", today),
                        existing.get("yen"), existing.get("percent")])
    if len(entries) >= 2 and entries[-1][0] == today:
        entries[-1][1:] = [d.yen, d.percent]
        if entries[-1][1:] == entries[-2][1:]:
            entries.pop()
    else:
        entries.append([today, d.yen, d.percent])
    if len(entries) < 2:  # 取り消しで変化点が無くなったらキーごと消す
        del history[key]


def prune_history(history: dict, store: dict) -> int:
    """削除・掲載終了など表示され得なくなった案件の履歴を落とし、削除件数を返す
    （履歴ファイルのサイズ抑制）。"""
    deals = store["deals"]
    stale = [k for k in history if k not in deals or not is_visible(deals[k])]
    for k in stale:
        del history[k]
    return len(stale)


def known_ids(store: dict, site_key: str) -> set[str]:
    """指定サイトの既知 deal_id 集合を返す"""
    prefix = site_key + ":"
    return {k.split(":", 1)[1] for k in store["deals"] if k.startswith(prefix)}


def filled_ids(store: dict, site_key: str) -> set[str]:
    """タイトル取得済み（詳細が埋まっている）ID集合を返す。

    バックフィルの再開時に、既に取得済みの案件を再取得しないためのスキップ集合。
    """
    prefix = site_key + ":"
    return {
        k.split(":", 1)[1]
        for k, v in store["deals"].items()
        if k.startswith(prefix) and v.get("title")
    }


def _reward_change(existing: dict, d) -> int:
    """既知案件の報酬の増減を比較する。1=増加 / -1=減少 / 0=同額・比較不能。
    円換算(yen)を優先し、無ければ%(percent)で比べる。新旧で型が異なる（固定pt⇄%還元の
    切替など）場合は比較不能として0を返す（誤ったUP判定を避ける）。"""
    old_yen, new_yen = existing.get("yen"), d.yen
    if old_yen is not None and new_yen is not None:
        return (new_yen > old_yen) - (new_yen < old_yen)
    old_pct, new_pct = existing.get("percent"), d.percent
    if old_pct is not None and new_pct is not None:
        return (new_pct > old_pct) - (new_pct < old_pct)
    return 0


def _recently_new(existing: dict, today: str) -> bool:
    """初出または再浮上が直近NEW_DAYS日以内（＝現在サイトで新着/UP表示中）か。
    builder/generate.py の _is_new_or_up と同じ判定基準。"""
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=NEW_DAYS - 1)).strftime("%Y-%m-%d")
    is_new = not existing.get("backfill") and existing.get("first_seen", "") >= cutoff
    return is_new or (existing.get("renewed_at") or "")[:10] >= cutoff


def apply_renewal(existing: dict, d, now: str) -> bool:
    """既知案件の再掲載でポイントが増えていたら「再新着（ポイントUP）」として記録する。
    増加: renewed_at（再浮上日時）と renewed_from（旧ポイント表記。○P→○Pの表示用）を記録。
    減少: 増額期間の終了（例: 土日超還元→平日に戻る）なのでUP記録を消す。あわせて
          renew_hold を立て、バッジ由来の再新着（upsertのsite_newルール）を次の増額まで
          保留する（減額後にサイト側のUPバッジだけが残っていても新着へ再浮上させない）。
    新たにUPを記録したら True を返す。"""
    change = _reward_change(existing, d)
    if change > 0 and existing.get("title") and not existing.get("seeded"):
        already_up = bool(existing.get("renewed_at"))
        existing["renewed_at"] = now
        existing.pop("renew_hold", None)  # 増額したので保留を解除（バッジ検知を再開）
        # 連日の再増額でも「UP開始前の元ポイント」を保つ（毎回上書きすると増分が見えなくなる）
        if not already_up:
            existing["renewed_from"] = existing.get("points_text", "")
        return not already_up
    if change < 0:
        existing.pop("renewed_at", None)
        existing.pop("renewed_from", None)
        existing["renew_hold"] = True
    return False


def upsert(store: dict, deals: list, today: str, now: str | None = None,
           history: dict | None = None) -> list[str]:
    """取得した案件を反映し、新規追加された案件キーのリストを返す。

    now（"YYYY-MM-DD HH:MM"）を渡すと、新規案件に自HPへの初出日時（first_seen_at・
    時刻付き）を記録する。新着セクションの掲載日時表示・新着順ソートに用いる。
    history（load_history の辞書）を渡すと、既知案件の報酬変化を値動き履歴に記録する。
    """
    new_keys = []
    for d in deals:
        key = f"{d.site}:{d.deal_id}"
        existing = store["deals"].get(key)
        if existing is None:
            store["deals"][key] = {
                "site": d.site,
                "deal_id": d.deal_id,
                "title": d.title,
                "points_text": d.points_text,
                "yen": d.yen,
                "percent": d.percent,
                "url": d.url,
                "condition": d.condition,
                "seeded": d.seeded,
                "first_seen": today,
                "first_seen_at": now or today,  # 自HP初出日時（時刻付き。掲載日時表示・新着順用）
                "last_seen": today,
            }
            if not d.seeded:
                new_keys.append(key)
        else:
            # 既知案件はポイント数などの最新値だけ更新する。
            # seededフラグは維持する（解除すると初回シード分が翌日「新着」扱いで溢れる）
            # 上書き前に報酬の変化点を値動き履歴に記録する（UPランキング・値動きページ用）
            if history is not None:
                record_history(history, key, existing, d, today)
            # 上書き前にポイント増減を比較し、増えていれば「再新着（ポイントUP）」として記録する
            renewed = apply_renewal(existing, d, now or today)
            # 一覧アイテムにサイト側のNEW/UPバッジが付いている既知案件（list_new_markers設定
            # サイト）は、新着/UP表示が切れていれば再新着にする。増額済みの値しか観測できず
            # 差分が出ない案件（週末だけ再掲載される増額案件など）を拾う。状態は持たず
            # 「バッジあり かつ 現在新着/UP表示でない」時のみ記録する（バッジが続く限り維持）。
            # renew_hold（減額を観測済み）の案件は、バッジが残っていても次の増額まで対象外
            # （減額された案件を新着に再浮上させないため）
            if (not renewed and d.site_new and existing.get("title")
                    and not existing.get("seeded") and not existing.get("renew_hold")
                    and not _recently_new(existing, today)):
                existing["renewed_at"] = now or today
            existing.update(
                title=d.title or existing["title"],
                points_text=d.points_text or existing["points_text"],
                yen=d.yen if d.yen is not None else existing.get("yen"),
                percent=d.percent if d.percent is not None else existing.get("percent"),
                url=d.url or existing["url"],
                condition=d.condition or existing.get("condition", ""),
                last_seen=today,
            )
    return new_keys


def upsert_backfill(store: dict, deals: list, today: str) -> int:
    """一度きりの全件バックフィルで取得した案件を反映し、新たに埋めた件数を返す。

    - 未登録: backfill案件として追加（first_seen は出の日付 or 2000-01-01）
    - 既存の未取得ID(seeded)や既存backfill: タイトル等を埋め、表示対象にする
    - 既存の通常案件（日次で取得済みの可視案件）: 初出日や新着判定を壊さないよう触らない
    """
    added = 0
    for d in deals:
        key = f"{d.site}:{d.deal_id}"
        first_seen = d.first_seen_override or "2000-01-01"
        existing = store["deals"].get(key)
        if existing is None:
            store["deals"][key] = {
                "site": d.site,
                "deal_id": d.deal_id,
                "title": d.title,
                "points_text": d.points_text,
                "yen": d.yen,
                "percent": d.percent,
                "url": d.url,
                "condition": d.condition,
                "seeded": False,
                "backfill": True,
                "first_seen": first_seen,
                "last_seen": today,
            }
            if d.title:
                added += 1
        elif existing.get("seeded") or existing.get("backfill"):
            was_empty = not existing.get("title")
            existing.update(
                title=d.title or existing.get("title", ""),
                points_text=d.points_text or existing.get("points_text", ""),
                yen=d.yen if d.yen is not None else existing.get("yen"),
                percent=d.percent if d.percent is not None else existing.get("percent"),
                url=d.url or existing.get("url", ""),
                condition=d.condition or existing.get("condition", ""),
                seeded=False,
                backfill=True,
                last_seen=today,
            )
            # seeded から初めて埋めた場合のみ初出日を設定（既存backfillの日付は保持）
            if not existing.get("backfill_dated"):
                existing["first_seen"] = first_seen
                existing["backfill_dated"] = True
            if was_empty and d.title:
                added += 1
        else:
            # 通常の可視案件はバックフィルで上書きしない（日付・新着判定の保全）
            existing["last_seen"] = today
    return added


def purge_backfill(store: dict) -> int:
    """バックフィルで登録した案件を全削除し、削除件数を返す（後片付け用）。"""
    keys = [k for k, v in store["deals"].items() if v.get("backfill")]
    for k in keys:
        del store["deals"][k]
    return len(keys)


def is_visible(deal: dict) -> bool:
    """サイトに表示され得る案件か（タイトル有・初回シードでない・掲載終了でない）"""
    return bool(deal.get("title")) and not deal.get("seeded") and not deal.get("delisted_at")


def recent_visible(store: dict, today: str, days: int | None = RECENT_DAYS) -> list[dict]:
    """表示対象案件を返す（サイト生成とリンクチェックの共通母集団）。
    days を指定すると初出がその日数以内のものに絞り、None なら全期間を対象にする。
    バックフィル案件は日付フィルタに関わらず常に表示対象にする（全案件掲載が目的のため）。"""
    visible = [d for d in store["deals"].values() if is_visible(d)]
    if days is None:
        return visible
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return [d for d in visible if d.get("backfill") or d["first_seen"] >= cutoff]
