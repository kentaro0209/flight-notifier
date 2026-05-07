"""
Add a flight to schedule.csv from a flight number and departure-local date.

This is used by the "Add Flight" GitHub Actions workflow so schedule.csv can be
updated without hand-editing CSV rows.
"""

import csv
import os
import sys
from pathlib import Path

import requests


AERODATABOX_API_KEY = os.environ["AERODATABOX_RAPIDAPI_KEY"]
AERODATABOX_HOST = os.environ.get("AERODATABOX_RAPIDAPI_HOST", "aerodatabox.p.rapidapi.com")
SCHEDULE_FILE = Path("schedule.csv")
FIELDNAMES = [
    "flight_iata",
    "flight_date",
    "scheduled_departure",
    "scheduled_arrival",
    "note",
]


def datetime_value(value: dict | str | None) -> str:
    if isinstance(value, dict):
        return value.get("utc") or value.get("local") or ""
    return value or ""


def airport_code(airport: dict) -> str:
    return airport.get("iata") or airport.get("icao") or "?"


def fetch_flight(flight_iata: str, flight_date: str) -> dict:
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

    response = requests.get(url, headers=headers, params=params, timeout=20)
    if response.status_code == 204:
        raise RuntimeError(f"{flight_iata} {flight_date}: flight data not found")
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{flight_iata} {flight_date}: flight data not found")
    return data[0]


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


def build_row(flight_iata: str, flight_date: str, raw: dict) -> dict:
    departure = raw.get("departure") or {}
    arrival = raw.get("arrival") or {}
    dep_airport = departure.get("airport") or {}
    arr_airport = arrival.get("airport") or {}

    scheduled_departure = datetime_value(departure.get("scheduledTime"))
    scheduled_arrival = datetime_value(arrival.get("scheduledTime"))
    if not scheduled_departure:
        raise RuntimeError(f"{flight_iata} {flight_date}: scheduled departure is missing")

    dep_code = airport_code(dep_airport)
    arr_code = airport_code(arr_airport)
    note = f"{dep_code}-{arr_code}" if dep_code != "?" or arr_code != "?" else ""

    return {
        "flight_iata": flight_iata,
        "flight_date": flight_date,
        "scheduled_departure": scheduled_departure,
        "scheduled_arrival": scheduled_arrival,
        "note": note,
    }


def upsert_row(rows: list[dict], new_row: dict) -> tuple[list[dict], str]:
    key = (new_row["flight_iata"], new_row["flight_date"])
    updated = False
    result = []

    for row in rows:
        row_key = (row.get("flight_iata"), row.get("flight_date"))
        if row_key == key:
            result.append(new_row)
            updated = True
        else:
            result.append({field: row.get(field, "") for field in FIELDNAMES})

    if updated:
        return result, "updated"

    result.append(new_row)
    return result, "added"


def main() -> None:
    flight_iata = os.environ.get("FLIGHT_IATA", "").strip().upper().replace(" ", "")
    flight_date = os.environ.get("FLIGHT_DATE", "").strip()

    if not flight_iata or not flight_date:
        raise SystemExit("FLIGHT_IATA and FLIGHT_DATE are required")

    raw = fetch_flight(flight_iata, flight_date)
    new_row = build_row(flight_iata, flight_date, raw)
    rows, action = upsert_row(load_rows(), new_row)
    save_rows(rows)

    print(f"{action}: {new_row['flight_iata']} {new_row['flight_date']} {new_row['note']}")
    print(f"departure: {new_row['scheduled_departure']}")
    print(f"arrival: {new_row['scheduled_arrival'] or '?'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise SystemExit(1)
