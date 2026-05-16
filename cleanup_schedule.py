"""Remove past flights from schedule.csv."""

import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path


SCHEDULE_FILE = Path("schedule.csv")
JST = timezone(timedelta(hours=9))
FIELDNAMES = [
    "flight_number",
    "flight_date",
    "scheduled_departure",
    "scheduled_arrival",
    "note",
]


def main() -> None:
    today = datetime.now(JST).date().isoformat()
    if not SCHEDULE_FILE.exists():
        return

    with SCHEDULE_FILE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    kept = [
        {field: row.get(field, "") for field in FIELDNAMES}
        for row in rows
        if row.get("flight_date", "") >= today
    ]
    removed = len(rows) - len(kept)

    with SCHEDULE_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(kept)

    print(f"removed={removed}")


if __name__ == "__main__":
    main()
