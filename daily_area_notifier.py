"""Send daily Discord notifications for registered user areas."""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


JST = timezone(timedelta(hours=9))
USERS_FILE = Path("data/users.json")
STATE_FILE = Path("area_notify_state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NOTIFY_WINDOW_MIN = int(os.environ.get("NOTIFY_WINDOW_MIN", "10"))
FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "").lower() in {"1", "true", "yes"}
DAILY_NOTIFY = os.environ.get("DAILY_NOTIFY", "").lower() in {"1", "true", "yes"}


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_notify_time(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def is_due(user: dict, now: datetime) -> bool:
    if FORCE_NOTIFY or DAILY_NOTIFY:
        return True

    notify_time = parse_notify_time(user.get("notify_time", ""))
    if not notify_time:
        return False

    hour, minute = notify_time
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target <= now < target + timedelta(minutes=NOTIFY_WINDOW_MIN)


def already_sent(state: dict, discord_user_id: str, date_key: str) -> bool:
    return state.get(discord_user_id, {}).get("last_notified_date") == date_key


def mark_sent(state: dict, discord_user_id: str, date_key: str, now: datetime) -> None:
    state[discord_user_id] = {
        "last_notified_date": date_key,
        "last_notified_at": now.isoformat(),
    }


def build_message(discord_user_id: str, user: dict, now: datetime) -> str:
    area_keywords = user.get("area_keywords") or []
    child_keywords = user.get("child_keywords") or []
    address = " ".join(
        value for value in [
            user.get("prefecture", ""),
            user.get("city", ""),
            user.get("town", ""),
        ]
        if value
    )

    lines = [
        f"<@{discord_user_id}> 今日の地域通知です",
        f"日付: {now.strftime('%Y-%m-%d')}",
    ]
    if address:
        lines.append(f"登録地域: {address}")
    if area_keywords:
        lines.append(f"地域キーワード: {', '.join(area_keywords)}")
    if child_keywords:
        lines.append(f"子ども関連キーワード: {', '.join(child_keywords)}")
    lines.append("この条件でイベント情報を確認してください。")
    return "\n".join(lines)


def send_discord(text: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL が設定されていません")

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={
            "content": text[:2000],
            "allowed_mentions": {"parse": ["users"]},
        },
        timeout=20,
    )
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"Discord webhook {response.status_code}: {response.text}")


def main() -> None:
    now = datetime.now(JST)
    date_key = now.date().isoformat()
    users = load_json(USERS_FILE, {})
    state = load_json(STATE_FILE, {})

    sent_count = 0
    for discord_user_id, user in users.items():
        if not isinstance(user, dict):
            print(f"[WARN] invalid user settings: {discord_user_id}", file=sys.stderr)
            continue
        if not is_due(user, now):
            continue
        if not FORCE_NOTIFY and already_sent(state, discord_user_id, date_key):
            continue

        send_discord(build_message(discord_user_id, user, now))
        mark_sent(state, discord_user_id, date_key, now)
        sent_count += 1

    save_json(STATE_FILE, state)
    print(f"sent_count={sent_count}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise SystemExit(1)
