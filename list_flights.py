"""Send the currently registered flight schedule to LINE."""

import csv
import os
from datetime import datetime
from pathlib import Path

import requests


SCHEDULE_FILE = Path("schedule.csv")
LINE_CHANNEL_TOKEN = os.environ["LINE_CHANNEL_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]


def format_time(value: str) -> str:
    if not value:
        return "?"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%m/%d %H:%M")
    except ValueError:
        return value


def load_rows() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        return []
    with SCHEDULE_FILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sort_key(row: dict) -> tuple[str, str, str]:
    return (
        row.get("flight_date", ""),
        row.get("scheduled_departure", ""),
        row.get("flight_number", ""),
    )


def build_message(rows: list[dict]) -> str:
    if not rows:
        return "登録中の監視便はありません。"

    lines = ["登録中の監視便"]
    for row in sorted(rows, key=sort_key):
        lines.append(
            "\n"
            f"{row.get('flight_date', '?')} {row.get('flight_number', '?')}\n"
            f"{row.get('note', '')}\n"
            f"出発: {format_time(row.get('scheduled_departure', ''))}\n"
            f"到着: {format_time(row.get('scheduled_arrival', ''))}"
        )
    return "\n".join(lines)


def send_line(text: str) -> None:
    print(text)
    response = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
        },
        json={
            "to": LINE_USER_ID,
            "messages": [{"type": "text", "text": text[:5000]}],
        },
        timeout=20,
    )
    print(f"LINE response: {response.status_code} {response.text}")
    response.raise_for_status()


def main() -> None:
    send_line(build_message(load_rows()))


if __name__ == "__main__":
    main()
