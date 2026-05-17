"""Register a user's home search area from a Japanese postal code."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests


ZIPCLOUD_URL = "https://zipcloud.ibsnet.co.jp/api/search"
USERS_FILE = Path("data/users.json")
DEFAULT_CHILD_KEYWORDS = ["子ども", "親子", "小学生", "未就学児"]
DEFAULT_NOTIFY_TIME = "08:00"

SPECIAL_AREA_KEYWORDS = {
    ("東京都", "文京区", "湯島"): [
        "文京区",
        "湯島",
        "本郷",
        "御茶ノ水",
        "上野",
        "後楽園",
        "千代田区",
        "台東区",
    ],
}

TOKYO_WARD_NEIGHBORS = {
    "千代田区": ["中央区", "港区", "新宿区", "文京区", "台東区"],
    "中央区": ["千代田区", "港区", "台東区", "江東区"],
    "港区": ["千代田区", "中央区", "新宿区", "品川区", "渋谷区"],
    "新宿区": ["千代田区", "港区", "文京区", "渋谷区", "中野区", "豊島区"],
    "文京区": ["千代田区", "台東区", "豊島区", "北区", "荒川区"],
    "台東区": ["千代田区", "中央区", "文京区", "墨田区", "荒川区"],
    "墨田区": ["台東区", "江東区", "荒川区", "葛飾区", "江戸川区"],
    "江東区": ["中央区", "港区", "墨田区", "品川区", "江戸川区"],
    "品川区": ["港区", "江東区", "目黒区", "大田区", "渋谷区"],
    "目黒区": ["品川区", "大田区", "世田谷区", "渋谷区"],
    "大田区": ["品川区", "目黒区", "世田谷区"],
    "世田谷区": ["目黒区", "大田区", "渋谷区", "杉並区"],
    "渋谷区": ["港区", "新宿区", "品川区", "目黒区", "世田谷区", "中野区"],
    "中野区": ["新宿区", "渋谷区", "杉並区", "豊島区", "練馬区"],
    "杉並区": ["世田谷区", "渋谷区", "中野区", "練馬区"],
    "豊島区": ["新宿区", "文京区", "中野区", "北区", "板橋区", "練馬区"],
    "北区": ["文京区", "豊島区", "荒川区", "板橋区", "足立区"],
    "荒川区": ["文京区", "台東区", "墨田区", "北区", "足立区"],
    "板橋区": ["豊島区", "北区", "練馬区"],
    "練馬区": ["中野区", "杉並区", "豊島区", "板橋区"],
    "足立区": ["北区", "荒川区", "葛飾区"],
    "葛飾区": ["墨田区", "足立区", "江戸川区"],
    "江戸川区": ["墨田区", "江東区", "葛飾区"],
}


def validate_postal_code(value: str) -> str:
    postal_code = value.strip().replace("-", "")
    if not re.fullmatch(r"\d{7}", postal_code):
        raise RuntimeError("郵便番号は7桁で入力してください。例: /register 1130034")
    return postal_code


def fetch_address(postal_code: str) -> dict:
    response = requests.get(ZIPCLOUD_URL, params={"zipcode": postal_code}, timeout=20)
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != 200:
        raise RuntimeError(payload.get("message") or "ZipCloud APIで住所を取得できませんでした")

    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"郵便番号 {postal_code} の住所が見つかりませんでした")

    result = results[0]
    return {
        "prefecture": result.get("address1", ""),
        "city": result.get("address2", ""),
        "town": result.get("address3", ""),
    }


def unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def build_area_keywords(prefecture: str, city: str, town: str) -> list[str]:
    special = SPECIAL_AREA_KEYWORDS.get((prefecture, city, town))
    if special:
        return special

    keywords = [city, town]
    if prefecture == "東京都":
        keywords.extend(TOKYO_WARD_NEIGHBORS.get(city, [])[:3])
    return unique(keywords)


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def save_users(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def register_user_area(discord_user_id: str, postal_code: str) -> dict:
    if not discord_user_id.strip():
        raise RuntimeError("DiscordユーザーIDが空です")

    postal_code = validate_postal_code(postal_code)
    address = fetch_address(postal_code)
    area_keywords = build_area_keywords(
        address["prefecture"],
        address["city"],
        address["town"],
    )

    users = load_users()
    current = users.get(discord_user_id, {})
    users[discord_user_id] = {
        "postal_code": postal_code,
        "prefecture": address["prefecture"],
        "city": address["city"],
        "town": address["town"],
        "area_keywords": area_keywords,
        "child_keywords": current.get("child_keywords", DEFAULT_CHILD_KEYWORDS),
        "notify_time": current.get("notify_time", DEFAULT_NOTIFY_TIME),
    }
    save_users(users)
    return users[discord_user_id]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register area settings from a postal code.")
    parser.add_argument("discord_user_id", nargs="?", default=os.environ.get("DISCORD_USER_ID", ""))
    parser.add_argument("postal_code", nargs="?", default=os.environ.get("POSTAL_CODE", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user = register_user_area(args.discord_user_id, args.postal_code)
    print(
        "地域を登録しました\n"
        f"郵便番号: {user['postal_code']}\n"
        f"住所: {user['prefecture']} {user['city']} {user['town']}\n"
        f"検索キーワード: {', '.join(user['area_keywords'])}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise SystemExit(1)
