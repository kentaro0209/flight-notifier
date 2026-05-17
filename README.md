# JALフライト出発・到着通知システム

JAL便の出発・到着・大幅遅延・欠航をLINEに通知します。データ取得は公共交通オープンデータセンター(ODPT API)を使います。

## 構成

| 項目 | 内容 |
|------|------|
| データ取得 | ODPT API |
| 通知 | LINE Messaging API |
| 実行基盤 | GitHub Actions |
| 状態管理 | `flight_state.json` を自動コミット |

## 必要なSecrets

GitHub の **Settings → Secrets and variables → Actions** に以下を登録します。

| Name | Value |
|------|-------|
| `ODPT_TOKEN` | ODPTのアクセストークン |
| `LINE_CHANNEL_TOKEN` | LINEのChannel access token |
| `LINE_USER_ID` | LINEのユーザーID |

`LINE_CHANNEL_TOKEN` と `LINE_USER_ID` は既存設定をそのまま使えます。`ODPT_TOKEN` は ODPT 開発者登録の承認後に追加してください。

## ODPTトークン取得

1. <https://developer.odpt.org/> にアクセス
2. ユーザー登録を行う
3. 承認後にログイン
4. 右上メニューなどから **ODPTセンター用アクセストークン** を開く
5. `DefaultApplication` などのアクセストークンをコピー
6. GitHub Secrets に `ODPT_TOKEN` として登録

## スケジュール登録

LINEで以下のように送ると、ODPT APIから予定時刻と区間を取得して `schedule.csv` に登録します。

```text
追加 JL567 2026-05-20
```

ODPTにまだ出ていない未来便は、外部時刻表サイトから予定時刻の取得を試みます。取得できない場合は、時刻も一緒に送ります。

```text
追加 JL567 2026-05-20 10:30 12:00 羽田→女満別
```

登録後、GitHub Actions の **Add Flight** が実行され、成功/失敗がLINEに届きます。

手動で編集する場合は、`schedule.csv` にJAL便を登録します。

```csv
flight_number,flight_date,scheduled_departure,scheduled_arrival,note
JL6,2026-05-15,2026-05-15T11:20:00+09:00,2026-05-15T10:30:00-04:00,成田→JFK
JL515,2026-05-20,2026-05-20T08:00:00+09:00,2026-05-20T09:30:00+09:00,羽田→札幌
```

| カラム | 必須 | 説明 |
|--------|------|------|
| `flight_number` | 必須 | `JL6` のような便名。ゼロ埋め不要 |
| `flight_date` | 必須 | 出発地のローカル日付 |
| `scheduled_departure` | 必須 | 出発予定時刻。タイムゾーン付きISO8601 |
| `scheduled_arrival` | 推奨 | 到着予定時刻。タイムゾーン付きISO8601 |
| `note` | 任意 | 通知に表示するメモ |

## 動作確認

GitHub Actions の **Flight Notifier** を開き、**Run workflow** で手動実行します。`ODPT_TOKEN` 登録前はODPT API取得ができないため、トークン登録後に確認してください。

## 監視便の確認

LINEで以下のように送ると、現在 `schedule.csv` に登録されている監視便一覧が届きます。

```text
一覧
```

GitHub Actions の **List Flights** を開き、**Run workflow** で手動実行しても同じ一覧を送信できます。

## Discord地域登録

Discordで以下のように送ると、郵便番号からZipCloud APIで住所を取得し、`data/users.json` に自宅周辺の検索地域を登録します。

```text
/register 1130034
```

登録される内容は次の形式です。

```json
{
  "discord_user_id": {
    "postal_code": "1130034",
    "prefecture": "東京都",
    "city": "文京区",
    "town": "湯島",
    "area_keywords": ["文京区", "湯島", "本郷", "御茶ノ水"],
    "child_keywords": ["子ども", "親子", "小学生", "未就学児"],
    "notify_time": "08:00"
  }
}
```

Discord botを動かす場合は、`DISCORD_TOKEN` を環境変数に設定して起動します。

```bash
pip install -r requirements.txt
python discord_bot.py
```

GitHub Actions の **Register Area** を手動実行しても、`discord_user_id` と `postal_code` から同じ登録処理を実行できます。

## Discord定期通知

GitHub Actions が毎日08:00 JSTにDiscordチャンネルへ通知します。PCでBotを常時起動する必要はありません。

通知先チャンネルで **チャンネル設定 → 連携サービス → ウェブフック** からWebhook URLを作成し、GitHub Secrets に以下を登録します。

| Name | Value |
|------|-------|
| `DISCORD_WEBHOOK_URL` | DiscordチャンネルのWebhook URL |

通知は `.github/workflows/daily-area-notifier.yml` が毎日08:00 JSTに起動し、登録済みユーザーへ送ります。同じユーザーには1日1回だけ送信し、送信済み状態は `area_notify_state.json` に保存されます。

手動で動作確認する場合は、GitHub Actions の **Daily Area Notifier** を開き、**Run workflow** で `force_notify` に `true` を入れて実行します。

## 通知内容

| イベント | 条件 |
|----------|------|
| 出発 | ODPTの出発 `actualTime` が記録された |
| 到着 | ODPTの到着 `actualTime` が記録された |
| 大幅遅延 | 出発予定より30分以上遅れる見込み |
| 欠航 | ステータスが `Cancelled` |
| 引き返し | ステータスが `TurnedBack` |
| 目的地変更 | ステータスが `Diverted` |

## 注意

- GitHub Actions は5分間隔で起動します。
- 監視対象は出発予定30分前から到着予定3時間後までです。
- `flight_state.json` は自動更新されます。
- LINEから便名だけで登録する仕組みは、ODPT_TOKEN取得後にODPT APIへ合わせて調整予定です。
