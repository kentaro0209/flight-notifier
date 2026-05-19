"""
JALフライト出発・到着通知スクリプト (ODPT API版)

データソース: 公共交通オープンデータセンター (ODPT)
  - JAL公式のリアルタイム出発/到着情報(国内線・国際線)
  - 完全無料・リクエスト制限なし

通知: LINE Messaging API
実行: GitHub Actions (5分間隔)
"""

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ===== 環境変数(GitHub Secretsから注入) =====
ODPT_TOKEN = os.environ["ODPT_TOKEN"]
LINE_CHANNEL_TOKEN = os.environ["LINE_CHANNEL_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

# ===== 定数 =====
JST = timezone(timedelta(hours=9))
ODPT_BASE = "https://api.odpt.org/api/v4"

SCHEDULE_FILE = Path("schedule.csv")
STATE_FILE = Path("flight_state.json")

# 監視ウィンドウ: 出発予定の何分前から / 到着予定の何分後まで
PRE_WINDOW_MIN = 30
POST_WINDOW_MIN = 180
# 大幅遅延とみなす閾値(分)
SIGNIFICANT_DELAY_MIN = 30
# 監視開始・データなしをLINEに出して、システムが黙らないようにする
NOTIFY_MONITOR_START = True
NOTIFY_NO_DATA_ONCE = True

# ODPT flightStatus の日本語マッピング
STATUS_MAP = {
    "odpt.FlightStatus:OnTime": "定刻",
    "odpt.FlightStatus:Delayed": "遅延",
    "odpt.FlightStatus:Cancelled": "欠航",
    "odpt.FlightStatus:Diverted": "目的地変更",
    "odpt.FlightStatus:ReturnedToGate": "ゲート戻り",
    "odpt.FlightStatus:Departed": "出発済",
    "odpt.FlightStatus:Arrived": "到着済",
    "odpt.FlightStatus:Landing": "着陸中",
    "odpt.FlightStatus:TurnedBack": "引き返し",
}


# ===== スケジュール管理 =====
def load_schedule() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        print(f"[ERROR] {SCHEDULE_FILE} が見つかりません", file=sys.stderr)
        return []
    flights = []
    with SCHEDULE_FILE.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            if row.get("flight_number"):
                flights.append(row)
    return flights


# ===== 状態管理 =====
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ===== 時刻ユーティリティ =====
def parse_schedule_time(s: str) -> datetime | None:
    """schedule.csvのISO8601時刻をdatetimeに"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_odpt_time(time_str: str | None, date_str: str) -> datetime | None:
    """ODPTの時刻(HH:MM)と日付(YYYY-MM-DD)を合成してdatetimeに"""
    if not time_str or not date_str:
        return None
    try:
        return datetime.fromisoformat(f"{date_str}T{time_str}:00+09:00")
    except ValueError:
        return None


def fmt_time(dt: datetime | None) -> str:
    if not dt:
        return "?"
    return dt.astimezone(JST).strftime("%m/%d %H:%M")


def in_monitoring_window(flight: dict, now: datetime) -> bool:
    sched_dep = parse_schedule_time(flight.get("scheduled_departure", ""))
    sched_arr = parse_schedule_time(flight.get("scheduled_arrival", ""))
    if not sched_dep:
        return False
    start = sched_dep - timedelta(minutes=PRE_WINDOW_MIN)
    end = (sched_arr or sched_dep) + timedelta(minutes=POST_WINDOW_MIN)
    return start <= now <= end


# ===== ODPT API =====
def fetch_odpt(data_type: str, flight_number: str) -> dict | None:
    """
    ODPTからフライト情報を取得
    data_type: "Departure" or "Arrival"
    flight_number: "JL0006" 形式(4桁ゼロ埋め)
    """
    url = f"{ODPT_BASE}/odpt:FlightInformation{data_type}"
    params = {
        "odpt:operator": "odpt.Operator:JAL",
        "odpt:flightNumber": flight_number,
        "acl:consumerKey": ODPT_TOKEN,
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[WARN] ODPT {data_type} {flight_number}: {e}", file=sys.stderr)
        return None

    if not data:
        return None

    # 複数日分返ることがあるので、最新のものを返す
    if isinstance(data, list):
        return data[0] if len(data) == 1 else max(data, key=lambda x: x.get("dc:date", ""))
    return data


def flight_number_candidates(iata: str) -> list[str]:
    """
    ODPT側の便名形式ゆれに備えて複数候補を試す。
    例: JL567 -> JL0567 / JL567
    """
    prefix = ""
    num = ""
    for i, c in enumerate(iata):
        if c.isdigit():
            prefix = iata[:i]
            num = iata[i:]
            break
    if not prefix:
        return [iata]

    raw = f"{prefix}{int(num)}"
    padded4 = f"{prefix}{num.zfill(4)}"
    padded3 = f"{prefix}{num.zfill(3)}"
    candidates = [padded4, raw, padded3]

    result = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def fetch_odpt_any(data_type: str, flight_numbers: list[str]) -> dict | None:
    for flight_number in flight_numbers:
        info = fetch_odpt(data_type, flight_number)
        if info:
            print(f"[OK] ODPT {data_type}: {flight_number}")
            return info
        print(f"[INFO] ODPT {data_type}: {flight_number} データなし")
    return None


# ===== LINE通知 =====
def send_line(text: str) -> None:
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
            print("[OK] LINE送信成功")
    except Exception as e:
        print(f"[ERROR] LINE送信例外: {e}", file=sys.stderr)


# ===== メッセージ組み立て =====
def build_message(
    label: str,
    flight_iata: str,
    dep_info: dict | None,
    arr_info: dict | None,
    flight: dict,
    extra: str = "",
) -> str:
    lines = [label, f"便名: {flight_iata}"]

    # 出発情報
    if dep_info:
        dep_airport = dep_info.get("odpt:departureAirport", "").replace("odpt.Airport:", "")
        dest_airport = dep_info.get("odpt:destinationAirport", "").replace("odpt.Airport:", "")
        lines.append(f"区間: {dep_airport} → {dest_airport}")

        date_str = dep_info.get("dc:date", "")[:10]
        sched = parse_odpt_time(dep_info.get("odpt:scheduledTime"), date_str)
        est = parse_odpt_time(dep_info.get("odpt:estimatedTime"), date_str)
        actual = parse_odpt_time(dep_info.get("odpt:actualTime"), date_str)

        if actual:
            lines.append(f"出発実績: {fmt_time(actual)}")
        elif est:
            lines.append(f"出発見込み: {fmt_time(est)} (定刻 {fmt_time(sched)})")
        elif sched:
            lines.append(f"出発予定: {fmt_time(sched)}")

        status = dep_info.get("odpt:flightStatus", "")
        if status:
            lines.append(f"状態: {STATUS_MAP.get(status, status)}")

    # 到着情報
    if arr_info:
        date_str = arr_info.get("dc:date", "")[:10]
        sched = parse_odpt_time(arr_info.get("odpt:scheduledTime"), date_str)
        est = parse_odpt_time(arr_info.get("odpt:estimatedTime"), date_str)
        actual = parse_odpt_time(arr_info.get("odpt:actualTime"), date_str)

        if actual:
            lines.append(f"到着実績: {fmt_time(actual)}")
        elif est:
            lines.append(f"到着見込み: {fmt_time(est)} (定刻 {fmt_time(sched)})")
        elif sched:
            lines.append(f"到着予定: {fmt_time(sched)}")

        if not dep_info:
            status = arr_info.get("odpt:flightStatus", "")
            if status:
                lines.append(f"状態: {STATUS_MAP.get(status, status)}")

    if extra:
        lines.append("")
        lines.append(extra)

    # メモがあれば追加
    note = flight.get("note", "")
    if note:
        lines.append(f"({note})")

    return "\n".join(lines)


# ===== 便ごとの処理 =====
def process_flight(flight: dict, state: dict, now: datetime) -> None:
    flight_iata = flight["flight_number"]  # 例: JL6, JL006
    flight_date = flight.get("flight_date", "")
    key = f"{flight_iata}_{flight_date}"

    if not in_monitoring_window(flight, now):
        return

    prev = state.get(key, {})
    if prev.get("finalized"):
        print(f"[SKIP] {key}: 確定済み")
        return

    notified = prev.get("notified", [])
    if NOTIFY_MONITOR_START and "monitoring_started" not in notified:
        send_line(
            "監視を開始しました\n"
            f"便名: {flight_iata}\n"
            f"日付: {flight_date}\n"
            f"予定: {fmt_time(parse_schedule_time(flight.get('scheduled_departure', '')))}"
            f" → {fmt_time(parse_schedule_time(flight.get('scheduled_arrival', '')))}\n"
            f"({flight.get('note', '')})"
        )
        notified.append("monitoring_started")

    # ODPT側の便名形式ゆれに備えて複数形式で試す
    odpt_candidates = flight_number_candidates(flight_iata)

    # 出発情報・到着情報を取得
    dep_info = fetch_odpt_any("Departure", odpt_candidates)
    arr_info = fetch_odpt_any("Arrival", odpt_candidates)

    if not dep_info and not arr_info:
        print(f"[INFO] {key}: ODPT データなし(運航前 or 非運航日)")
        if NOTIFY_NO_DATA_ONCE and "no_data" not in notified:
            send_line(
                "ODPTデータがまだ見つかりません\n"
                f"便名: {flight_iata}\n"
                f"日付: {flight_date}\n"
                "監視は継続します。"
            )
            notified.append("no_data")
            prev["notified"] = notified
            prev["last_check"] = now.isoformat()
            state[key] = prev
        return

    # --- 状態判定 ---
    dep_status = (dep_info or {}).get("odpt:flightStatus", "")
    arr_status = (arr_info or {}).get("odpt:flightStatus", "")
    dep_actual = (dep_info or {}).get("odpt:actualTime")
    arr_actual = (arr_info or {}).get("odpt:actualTime")
    arrived_detected = bool(arr_actual) or "Arrived" in dep_status or "Arrived" in arr_status

    print(f"[{key}] dep_status={dep_status} arr_status={arr_status} "
          f"dep_actual={dep_actual} arr_actual={arr_actual}")

    # 1. 欠航
    if ("Cancelled" in dep_status or "Cancelled" in arr_status) and "cancelled" not in notified:
        send_line(build_message("❌ 欠航になりました", flight_iata, dep_info, arr_info, flight))
        notified.append("cancelled")
        prev["finalized"] = True

    # 2. 出発済み(actualTimeが入った)
    elif dep_actual and "departed" not in notified:
        send_line(build_message("🛫 出発しました", flight_iata, dep_info, arr_info, flight))
        notified.append("departed")

    # 3. 到着済み(actualTimeまたはArrivedステータスが入った)
    if arrived_detected and "arrived" not in notified:
        send_line(build_message("🛬 到着しました", flight_iata, dep_info, arr_info, flight))
        notified.append("arrived")
        prev["finalized"] = True

    # 3.5 到着予定時刻を過ぎたが、ODPT実績がまだない場合の予告
    sched_arrival = parse_schedule_time(flight.get("scheduled_arrival", ""))
    if (
        sched_arrival
        and now >= sched_arrival
        and "arrival_due" not in notified
        and "arrived" not in notified
    ):
        send_line(
            "到着予定時刻を過ぎました\n"
            f"便名: {flight_iata}\n"
            f"予定: {fmt_time(sched_arrival)}\n"
            "ODPT実績を確認中です。"
        )
        notified.append("arrival_due")

    # 4. 引き返し・ダイバート
    if "TurnedBack" in dep_status and "turnedback" not in notified:
        send_line(build_message("↩️ 引き返しました", flight_iata, dep_info, arr_info, flight))
        notified.append("turnedback")
        prev["finalized"] = True

    if "Diverted" in arr_status and "diverted" not in notified:
        send_line(build_message("↪️ 目的地が変更されました", flight_iata, dep_info, arr_info, flight))
        notified.append("diverted")

    # 5. 大幅遅延(出発前のみ・1回)
    if "departed" not in notified and "delay_warned" not in notified and dep_info:
        date_str = dep_info.get("dc:date", "")[:10]
        sched = parse_odpt_time(dep_info.get("odpt:scheduledTime"), date_str)
        est = parse_odpt_time(dep_info.get("odpt:estimatedTime"), date_str)
        if sched and est:
            delay_min = int((est - sched).total_seconds() / 60)
            if delay_min >= SIGNIFICANT_DELAY_MIN:
                send_line(build_message(
                    f"⏰ 大幅遅延の見込み(+{delay_min}分)",
                    flight_iata, dep_info, arr_info, flight,
                ))
                notified.append("delay_warned")

    # 状態保存
    prev["notified"] = notified
    prev["dep_status"] = dep_status
    prev["arr_status"] = arr_status
    prev["last_check"] = now.isoformat()
    state[key] = prev


def cleanup_old_state(state: dict, now: datetime) -> dict:
    cutoff = now - timedelta(days=7)
    return {
        k: v for k, v in state.items()
        if parse_schedule_time(v.get("last_check", "")) and
        parse_schedule_time(v["last_check"]) >= cutoff
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"=== 監視開始: {now.isoformat()} ===")

    flights = load_schedule()
    print(f"スケジュール: {len(flights)}件")

    state = load_state()
    state = cleanup_old_state(state, now)

    monitored = 0
    for flight in flights:
        try:
            if in_monitoring_window(flight, now):
                monitored += 1
            process_flight(flight, state, now)
        except Exception as e:
            print(f"[ERROR] {flight.get('flight_number')}: {e}", file=sys.stderr)

    print(f"監視対象: {monitored}件")
    save_state(state)
    print("=== 監視終了 ===")


if __name__ == "__main__":
    main()
