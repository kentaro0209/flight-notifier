"""
妻のフライト出発・到着通知スクリプト
- AeroDataBox APIでフライト状態を取得
- LINE Messaging APIで通知
- GitHub Actionsで5分間隔実行
- スケジュールCSVを読み込み、対象期間内のフライトのみ監視
- 状態確定後は監視を停止してAPI使用量を抑える
"""

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ===== 環境変数(GitHub Secretsから注入) =====
AERODATABOX_API_KEY = os.environ["AERODATABOX_RAPIDAPI_KEY"]
AERODATABOX_HOST = os.environ.get("AERODATABOX_RAPIDAPI_HOST", "aerodatabox.p.rapidapi.com")
LINE_CHANNEL_TOKEN = os.environ["LINE_CHANNEL_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

# ===== ファイルパス =====
SCHEDULE_FILE = Path("schedule.csv")
STATE_FILE = Path("flight_state.json")

# ===== 監視ウィンドウ設定 =====
# GitHub Actions自体は5分ごとに起動し、API呼び出しは重要時間帯だけに絞る
MIN_API_INTERVAL_MIN = 10
DEP_WINDOW_BEFORE_MIN = 45
DEP_WINDOW_AFTER_MIN = 120
ARR_WINDOW_BEFORE_MIN = 60
ARR_WINDOW_AFTER_MIN = 90
# 大幅遅延とみなす閾値(分)
SIGNIFICANT_DELAY_MIN = 30


def load_schedule() -> list[dict]:
    """schedule.csvを読み込む"""
    if not SCHEDULE_FILE.exists():
        print(f"[ERROR] {SCHEDULE_FILE} が見つかりません", file=sys.stderr)
        return []

    flights = []
    with SCHEDULE_FILE.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            if not row.get("flight_iata"):
                continue
            flights.append(row)
    return flights


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_iso(s: str) -> datetime | None:
    """ISO8601文字列をdatetimeに変換(タイムゾーン付き)"""
    if not s:
        return None
    try:
        # AviationStackは "2026-05-07T10:30:00+00:00" 形式
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def in_active_window(flight: dict, now: datetime) -> bool:
    """出発・到着の周辺など、APIを叩く価値が高い時間帯か判定"""
    sched_dep = parse_iso(flight.get("scheduled_departure", ""))
    sched_arr = parse_iso(flight.get("scheduled_arrival", ""))
    if not sched_dep:
        return False

    dep_start = sched_dep - timedelta(minutes=DEP_WINDOW_BEFORE_MIN)
    dep_end = sched_dep + timedelta(minutes=DEP_WINDOW_AFTER_MIN)
    if dep_start <= now <= dep_end:
        return True

    if sched_arr:
        arr_start = sched_arr - timedelta(minutes=ARR_WINDOW_BEFORE_MIN)
        arr_end = sched_arr + timedelta(minutes=ARR_WINDOW_AFTER_MIN)
        if arr_start <= now <= arr_end:
            return True

    return False


def should_poll_api(flight: dict, prev: dict, now: datetime) -> bool:
    """無料枠を守るため、重要時間帯かつ前回API呼び出しから十分空いた時だけ取得"""
    if not in_active_window(flight, now):
        return False

    last_api_check = parse_iso(prev.get("last_api_check", ""))
    if not last_api_check:
        return True

    return now - last_api_check >= timedelta(minutes=MIN_API_INTERVAL_MIN)


def datetime_value(value: dict | str | None) -> str:
    """AeroDataBoxのDateTimeContractからISO文字列を取り出す"""
    if isinstance(value, dict):
        return value.get("utc") or value.get("local") or ""
    return value or ""


def airport_value(airport: dict) -> dict:
    """AeroDataBoxの空港情報を既存メッセージ形式へ寄せる"""
    return {
        "airport": airport.get("name") or "?",
        "iata": airport.get("iata") or airport.get("icao") or "?",
    }


def normalize_status(status: str) -> str:
    """AeroDataBoxのstatusを既存状態名へ正規化"""
    status_map = {
        "Expected": "scheduled",
        "CheckIn": "scheduled",
        "Boarding": "scheduled",
        "GateClosed": "scheduled",
        "Delayed": "scheduled",
        "EnRoute": "active",
        "Departed": "active",
        "Approaching": "active",
        "Arrived": "landed",
        "Canceled": "cancelled",
        "Cancelled": "cancelled",
        "CanceledUncertain": "cancelled",
        "CancelledUncertain": "cancelled",
        "Diverted": "cancelled",
    }
    return status_map.get(status, "unknown")


def normalize_flight(raw: dict) -> dict:
    """AeroDataBoxレスポンスを既存ロジックが扱える形へ変換"""
    dep = raw.get("departure") or {}
    arr = raw.get("arrival") or {}

    return {
        "flight_status": normalize_status(raw.get("status", "")),
        "source_status": raw.get("status", ""),
        "airline": raw.get("airline") or {},
        "departure": {
            **airport_value(dep.get("airport") or {}),
            "scheduled": datetime_value(dep.get("scheduledTime")),
            "estimated": datetime_value(dep.get("revisedTime") or dep.get("predictedTime")),
            "actual": datetime_value(dep.get("runwayTime")) if raw.get("status") in {"Departed", "EnRoute", "Arrived"} else "",
        },
        "arrival": {
            **airport_value(arr.get("airport") or {}),
            "scheduled": datetime_value(arr.get("scheduledTime")),
            "estimated": datetime_value(arr.get("revisedTime") or arr.get("predictedTime")),
            "actual": datetime_value(arr.get("runwayTime")) if raw.get("status") == "Arrived" else "",
        },
    }


def fetch_flight(flight_iata: str, flight_date: str) -> dict | None:
    """AeroDataBoxからフライト情報を取得"""
    url = f"https://{AERODATABOX_HOST}/flights/number/{flight_iata}/{flight_date}"
    headers = {
        "X-RapidAPI-Key": AERODATABOX_API_KEY,
        "X-RapidAPI-Host": AERODATABOX_HOST,
        "Accept": "application/json",
    }
    params = {
        "dateLocalRole": "Departure",
        "withAircraftImage": "false",
        "withLocation": "false",
        "withFlightPlan": "false",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 204:
            print(f"[WARN] {flight_iata}: データなし", file=sys.stderr)
            return None
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            print(
                f"[WARN] {flight_iata}: APIエラー {r.status_code} {r.text or e}",
                file=sys.stderr,
            )
            return None
        data = r.json()
    except Exception as e:
        print(f"[WARN] {flight_iata}: API取得失敗 {e}", file=sys.stderr)
        return None

    if not isinstance(data, list) or not data:
        return None

    return normalize_flight(data[0])


def send_line(text: str) -> None:
    """LINE Messaging APIでpush通知"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}],
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            print(f"[ERROR] LINE送信失敗: {r.status_code} {r.text}", file=sys.stderr)
        else:
            print(f"[OK] LINE送信成功")
    except Exception as e:
        print(f"[ERROR] LINE送信例外: {e}", file=sys.stderr)


def format_time(iso_str: str) -> str:
    """ISO時刻を見やすい形式に(JST想定)"""
    dt = parse_iso(iso_str)
    if not dt:
        return "?"
    # JST変換
    jst = dt.astimezone(timezone(timedelta(hours=9)))
    return jst.strftime("%m/%d %H:%M")


def build_message(label: str, flight_iata: str, info: dict, extra: str = "") -> str:
    dep = info.get("departure") or {}
    arr = info.get("arrival") or {}
    airline = (info.get("airline") or {}).get("name", "")

    lines = [
        label,
        f"便名: {flight_iata} ({airline})",
        f"{dep.get('airport', '?')} ({dep.get('iata', '?')}) → {arr.get('airport', '?')} ({arr.get('iata', '?')})",
    ]
    # 出発時刻
    dep_actual = dep.get("actual")
    dep_estimated = dep.get("estimated")
    dep_scheduled = dep.get("scheduled")
    if dep_actual:
        lines.append(f"出発実績: {format_time(dep_actual)}")
    elif dep_estimated:
        lines.append(f"出発見込み: {format_time(dep_estimated)} (定刻 {format_time(dep_scheduled)})")
    else:
        lines.append(f"出発予定: {format_time(dep_scheduled)}")

    # 到着時刻
    arr_actual = arr.get("actual")
    arr_estimated = arr.get("estimated")
    arr_scheduled = arr.get("scheduled")
    if arr_actual:
        lines.append(f"到着実績: {format_time(arr_actual)}")
    elif arr_estimated:
        lines.append(f"到着見込み: {format_time(arr_estimated)} (定刻 {format_time(arr_scheduled)})")
    else:
        lines.append(f"到着予定: {format_time(arr_scheduled)}")

    if extra:
        lines.append("")
        lines.append(extra)
    return "\n".join(lines)


def calc_delay_min(scheduled: str, estimated: str) -> int:
    """遅延分数を計算"""
    s = parse_iso(scheduled)
    e = parse_iso(estimated)
    if not s or not e:
        return 0
    return int((e - s).total_seconds() / 60)


def process_flight(flight: dict, state: dict, now: datetime) -> None:
    """1便分の処理"""
    flight_iata = flight["flight_iata"]
    flight_date = flight.get("flight_date", "")
    key = f"{flight_iata}_{flight_date}"

    # 既に最終状態(landed/cancelled)に到達済みならスキップ
    prev = state.get(key, {})
    if prev.get("finalized"):
        print(f"[SKIP] {key}: 確定済み")
        return

    # API無料枠節約のため、出発・到着の周辺だけ監視する
    if not should_poll_api(flight, prev, now):
        print(f"[SKIP] {key}: API呼び出し対象外")
        return

    info = fetch_flight(flight_iata, flight_date)
    if info is None:
        return

    new_status = info.get("flight_status")
    old_status = prev.get("status")
    print(f"[{key}] {old_status} -> {new_status}")

    notified = prev.get("notified", [])
    dep = info.get("departure") or {}
    arr = info.get("arrival") or {}

    # 1. 欠航通知
    if new_status == "cancelled" and "cancelled" not in notified:
        send_line(build_message("❌ 欠航になりました", flight_iata, info))
        notified.append("cancelled")
        prev["finalized"] = True

    # 2. 出発通知(activeを初検知した場合も通知)
    if new_status == "active" and old_status != "active" and "departed" not in notified:
        send_line(build_message("🛫 出発しました", flight_iata, info))
        notified.append("departed")

    # 3. 到着通知(active -> landed)
    if new_status == "landed" and "landed" not in notified:
        send_line(build_message("🛬 到着しました", flight_iata, info))
        notified.append("landed")
        prev["finalized"] = True

    # 4. 大幅遅延通知(出発前のみ・1回まで)
    if new_status == "scheduled" and "delay_warned" not in notified:
        delay = calc_delay_min(dep.get("scheduled", ""), dep.get("estimated", ""))
        if delay >= SIGNIFICANT_DELAY_MIN:
            send_line(build_message(
                f"⏰ 大幅遅延の見込み(+{delay}分)",
                flight_iata, info,
            ))
            notified.append("delay_warned")

    # 状態を保存
    prev["status"] = new_status
    prev["notified"] = notified
    prev["last_api_check"] = now.isoformat()
    prev["last_check"] = now.isoformat()
    state[key] = prev


def cleanup_old_state(state: dict, now: datetime) -> dict:
    """古い状態(7日以上前)を削除"""
    cutoff = now - timedelta(days=7)
    cleaned = {}
    for key, val in state.items():
        last = parse_iso(val.get("last_check", ""))
        if last and last >= cutoff:
            cleaned[key] = val
    return cleaned


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"=== 監視開始: {now.isoformat()} ===")

    flights = load_schedule()
    print(f"スケジュール: {len(flights)}件")

    state = load_state()
    state = cleanup_old_state(state, now)

    for flight in flights:
        try:
            process_flight(flight, state, now)
        except Exception as e:
            print(f"[ERROR] {flight.get('flight_iata')}: {e}", file=sys.stderr)

    save_state(state)
    print("=== 監視終了 ===")


if __name__ == "__main__":
    main()
