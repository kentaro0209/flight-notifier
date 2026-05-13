"""Add a JAL flight to schedule.csv using ODPT flight information."""

import csv
from html.parser import HTMLParser
import os
import re
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests


ODPT_TOKEN = os.environ["ODPT_TOKEN"]
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
ODPT_BASE = "https://api.odpt.org/api/v4"
JST = timezone(timedelta(hours=9))
SCHEDULE_FILE = Path("schedule.csv")
FIELDNAMES = [
    "flight_number",
    "flight_date",
    "scheduled_departure",
    "scheduled_arrival",
    "note",
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def normalize_input_flight_number(value: str) -> str:
    value = value.strip().upper().replace(" ", "")
    match = re.fullmatch(r"([A-Z]{2})(0*)(\d{1,4})", value)
    if not match:
        raise RuntimeError(f"便名の形式が正しくありません: {value}")
    return f"{match.group(1)}{int(match.group(3))}"


def normalize_odpt_flight_number(value: str) -> str:
    match = re.fullmatch(r"([A-Z]{2})(\d{1,4})", value)
    if not match:
        return value
    return f"{match.group(1)}{match.group(2).zfill(4)}"


def parse_odpt_time(value: str | None, date: str) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(f"{date}T{value}:00+09:00").isoformat()


def parse_manual_time(value: str, date: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if re.fullmatch(r"\d{2}:\d{2}", value):
        return datetime.fromisoformat(f"{date}T{value}:00+09:00").isoformat()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError as e:
        raise RuntimeError(f"時刻の形式が正しくありません: {value}") from e


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_effective_range(block: str) -> tuple[date | None, date | None]:
    through = re.search(r"Effective\s+(\d{4}-\d{2}-\d{2})\s+through\s+(\d{4}-\d{2}-\d{2})", block)
    if through:
        return parse_date(through.group(1)), parse_date(through.group(2))

    until = re.search(r"Valid until\s+(\d{4}-\d{2}-\d{2})", block)
    if until:
        return None, parse_date(until.group(1))

    from_match = re.search(r"Effective from\s+(\d{4}-\d{2}-\d{2})", block)
    if from_match:
        return parse_date(from_match.group(1)), None

    return None, None


def in_effective_range(target: date, start: date | None, end: date | None) -> bool:
    return (start is None or start <= target) and (end is None or target <= end)


def strip_tags(html: str) -> list[str]:
    parser = TextExtractor()
    parser.feed(html)
    return parser.parts


def fetch_flightmapper_row(flight_number: str, flight_date: str) -> dict | None:
    match = re.fullmatch(r"([A-Z]{2})(\d{1,4})", flight_number)
    if not match:
        return None

    airline = "JAL" if match.group(1) == "JL" else match.group(1)
    url = f"https://info.flightmapper.net/flight/{airline}_{match.group(1)}_{int(match.group(2))}"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    text = "\n".join(strip_tags(response.text))
    target = parse_date(flight_date)

    for block in text.split(f"{airline} {match.group(1)}{int(match.group(2))}")[1:]:
        start, end = parse_effective_range(block)
        if not in_effective_range(target, start, end):
            continue

        times = re.findall(r"\b\d{2}:\d{2}\b", block)
        airports = re.findall(r"([A-Za-z ]+ \(([A-Z]{3})\))", block)
        if len(times) < 2 or len(airports) < 2:
            continue

        dep_time, arr_time = times[0], times[1]
        dep_dt = datetime.fromisoformat(f"{flight_date}T{dep_time}:00+09:00")
        arr_dt = datetime.fromisoformat(f"{flight_date}T{arr_time}:00+09:00")
        if arr_dt <= dep_dt:
            arr_dt += timedelta(days=1)

        dep_code = airports[0][1]
        arr_code = airports[1][1]
        return {
            "flight_number": flight_number,
            "flight_date": flight_date,
            "scheduled_departure": dep_dt.isoformat(),
            "scheduled_arrival": arr_dt.isoformat(),
            "note": f"{dep_code}→{arr_code}",
        }

    return None


def compact_airport(value: str) -> str:
    return value.replace("odpt.Airport:", "") if value else "?"


def fetch_odpt(data_type: str, odpt_flight_number: str) -> dict | None:
    response = requests.get(
        f"{ODPT_BASE}/odpt:FlightInformation{data_type}",
        params={
            "odpt:operator": "odpt.Operator:JAL",
            "odpt:flightNumber": odpt_flight_number,
            "acl:consumerKey": ODPT_TOKEN,
        },
        timeout=20,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"ODPT API {response.status_code}: {response.text or e}") from e

    data = response.json()
    if not data:
        return None
    if isinstance(data, list):
        return data[0] if len(data) == 1 else max(data, key=lambda row: row.get("dc:date", ""))
    return data


def load_rows() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        return []
    with SCHEDULE_FILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict]) -> None:
    with SCHEDULE_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def send_line(text: str) -> None:
    if not LINE_CHANNEL_TOKEN or not LINE_USER_ID:
        return
    response = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
        timeout=20,
    )
    if response.status_code != 200:
        print(f"[WARN] LINE送信失敗: {response.status_code} {response.text}", file=sys.stderr)


def build_row(flight_number: str, flight_date: str, dep_info: dict, arr_info: dict | None) -> dict:
    data_date = dep_info.get("dc:date", "")[:10] or flight_date
    scheduled_departure = parse_odpt_time(dep_info.get("odpt:scheduledTime"), data_date)
    if not scheduled_departure:
        raise RuntimeError(f"{flight_number} の出発予定時刻がODPTから取得できませんでした")

    arr_date = (arr_info or {}).get("dc:date", "")[:10] or data_date
    scheduled_arrival = parse_odpt_time((arr_info or {}).get("odpt:scheduledTime"), arr_date)

    dep_airport = compact_airport(dep_info.get("odpt:departureAirport", ""))
    arr_airport = compact_airport(dep_info.get("odpt:destinationAirport", ""))
    return {
        "flight_number": flight_number,
        "flight_date": flight_date,
        "scheduled_departure": scheduled_departure,
        "scheduled_arrival": scheduled_arrival,
        "note": f"{dep_airport}→{arr_airport}",
    }


def build_manual_row(
    flight_number: str,
    flight_date: str,
    scheduled_departure: str,
    scheduled_arrival: str,
    note: str,
) -> dict:
    departure = parse_manual_time(scheduled_departure, flight_date)
    if not departure:
        raise RuntimeError(
            "ODPTに未掲載の便です。未来便は時刻つきで送ってください: "
            "追加 JL567 2026-05-20 10:30 12:00 羽田→女満別"
        )
    return {
        "flight_number": flight_number,
        "flight_date": flight_date,
        "scheduled_departure": departure,
        "scheduled_arrival": parse_manual_time(scheduled_arrival, flight_date),
        "note": note,
    }


def upsert_row(rows: list[dict], new_row: dict) -> tuple[list[dict], str]:
    key = (new_row["flight_number"], new_row["flight_date"])
    result = []
    updated = False
    for row in rows:
        row_key = (row.get("flight_number"), row.get("flight_date"))
        if row_key == key:
            result.append(new_row)
            updated = True
        else:
            result.append({field: row.get(field, "") for field in FIELDNAMES})
    if not updated:
        result.append(new_row)
    return result, "updated" if updated else "added"


def main() -> None:
    flight_number = normalize_input_flight_number(os.environ.get("FLIGHT_NUMBER", ""))
    flight_date = os.environ.get("FLIGHT_DATE", "").strip()
    manual_departure = os.environ.get("SCHEDULED_DEPARTURE", "").strip()
    manual_arrival = os.environ.get("SCHEDULED_ARRIVAL", "").strip()
    manual_note = os.environ.get("FLIGHT_NOTE", "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", flight_date):
        raise RuntimeError(f"日付の形式が正しくありません: {flight_date}")

    odpt_flight_number = normalize_odpt_flight_number(flight_number)
    dep_info = fetch_odpt("Departure", odpt_flight_number)
    arr_info = fetch_odpt("Arrival", odpt_flight_number)
    if dep_info:
        row = build_row(flight_number, flight_date, dep_info, arr_info)
        if manual_note:
            row["note"] = manual_note
    else:
        row = fetch_flightmapper_row(flight_number, flight_date)
        if row and manual_note:
            row["note"] = manual_note
        if not row:
            row = build_manual_row(
                flight_number,
                flight_date,
                manual_departure,
                manual_arrival,
                manual_note,
            )
    rows, action = upsert_row(load_rows(), row)
    save_rows(rows)

    print(f"{action}: {row}")
    send_line(
        "フライト予定を登録しました\n"
        f"便名: {row['flight_number']}\n"
        f"日付: {row['flight_date']}\n"
        f"区間: {row['note']}\n"
        f"出発: {row['scheduled_departure']}\n"
        f"到着: {row['scheduled_arrival'] or '?'}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        send_line(f"フライト予定の登録に失敗しました\n{e}")
        raise SystemExit(1)
