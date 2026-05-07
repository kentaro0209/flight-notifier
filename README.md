# 妻のフライト出発・到着通知システム

GitHub Actionsを5分おきに起動しつつ、AeroDataBox APIは出発・到着の周辺だけに絞って監視します。出発・到着・大幅遅延・欠航時にLINEへ通知します。

## 構成

- **データ取得**: AeroDataBox API (RapidAPI経由の無料枠あり)
- **通知**: LINE Messaging API (無料枠 月200通)
- **実行基盤**: GitHub Actions (パブリックリポジトリなら無制限)
- **状態管理**: `flight_state.json` をリポジトリに自動コミット

## セットアップ手順

### 1. AeroDataBox APIキーを取得

1. <https://rapid.aerodatabox.com/> から RapidAPI の AeroDataBox ページを開く
2. RapidAPI にログイン、またはアカウント作成
3. AeroDataBox API の無料プランに Subscribe
4. RapidAPI のエンドポイント画面で **X-RapidAPI-Key** をコピー
   → これが `AERODATABOX_RAPIDAPI_KEY`

### 2. LINE Messaging API を準備

1. <https://developers.line.biz/console/> にログイン(LINEアカウントで可)
2. 新しい **Provider** を作成(任意の名前)
3. 「Create a Messaging API channel」で新規チャネル作成
4. チャネル設定画面の **Messaging API** タブを開く
5. 一番下の **Channel access token** で「Issue」ボタンを押してトークン発行
   → これが `LINE_CHANNEL_TOKEN`
6. **Basic settings** タブの一番下にあるQRコードをスマホのLINEで読み取り、Bot を友達追加
7. ユーザーIDを取得(下記参照)
   → これが `LINE_USER_ID`

#### ユーザーID(LINE_USER_ID)の取得方法

最も簡単なのは LINE 公式の **Webhook** を使う方法ですが、面倒なので以下の手抜き手順:

1. チャネル設定画面の **Messaging API** タブ → **Webhook URL** を空のまま、**Use webhook** を OFF のままで OK
2. 同じタブ内、または **Basic settings** タブで **Your user ID**(自分のLINEユーザーID)が確認できます
   → 自分宛にだけ送るならこれを使えばOK
   - 注: ここで表示される「Your user ID」は LINE Developers コンソール所有者のIDです。Bot を友達追加した自分自身のIDなので、自分宛通知ならこれで動きます

### 3. リポジトリの準備

1. このディレクトリの内容を新しい GitHub リポジトリにpush
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<あなたのユーザー名>/flight-notifier.git
   git push -u origin main
   ```
2. **Public リポジトリ推奨**(Privateでも動くがActions分数を消費)

### 4. GitHub Secrets の設定

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録:

| Name | Value |
|------|-------|
| `AERODATABOX_RAPIDAPI_KEY` | RapidAPIのAeroDataBox用 `X-RapidAPI-Key` |
| `LINE_CHANNEL_TOKEN` | LINEのChannel access token |
| `LINE_USER_ID` | LINEのユーザーID(`U`から始まる文字列) |

### 5. ワークフロー権限の設定

**Settings → Actions → General → Workflow permissions** で以下を選択:

- ✅ **Read and write permissions**

これで Actions が `flight_state.json` をリポジトリにコミットできます。

### 6. スケジュール登録

リポジトリの **Actions** タブから **Add Flight** を選び、**Run workflow** で以下を入力します:

| 入力 | 例 | 説明 |
|------|-----|------|
| `flight_iata` | `JL006` | 便名(IATAコード) |
| `flight_date` | `2026-05-10` | 運航日(出発地のローカル日付・YYYY-MM-DD) |

実行すると AeroDataBox から出発予定時刻・到着予定時刻・区間を取得し、`schedule.csv` に自動で追記します。同じ便名・日付が既にある場合は更新します。

#### LINEから登録する場合

Google Apps Script をWebhook受け口にすると、LINEで以下のように送るだけで登録できます:

```text
追加 JL006 2026-05-10
```

手順:

1. <https://script.google.com/> で新しい Apps Script を作成
2. このリポジトリの `line_webhook_gas.js` の内容を貼り付け
3. Apps Script の **プロジェクトの設定 → スクリプト プロパティ** に以下を登録

| Name | Value |
|------|-------|
| `GITHUB_TOKEN` | GitHub workflow を起動できる Personal access token |
| `LINE_CHANNEL_ACCESS_TOKEN` | `LINE_CHANNEL_TOKEN` と同じLINEのChannel access token |

4. **デプロイ → 新しいデプロイ → ウェブアプリ** を選択
5. 実行ユーザーは **自分**、アクセスできるユーザーは **全員** にしてデプロイ
6. 発行されたWebアプリURLを LINE Developers Console の **Messaging API → Webhook URL** に設定
7. **Use webhook** を ON にする
8. LINEでBotに `追加 JL006 2026-05-10` のように送信

GitHub token は fine-grained token の場合、対象リポジトリを `kentaro0209/flight-notifier` に限定し、Actions の write 権限を付けます。

LINEの **Verify** で `A timeout occurred when sending a webhook event object` が出る場合は、Apps Script のデプロイ設定を確認します:

- 種類: **ウェブアプリ**
- 次のユーザーとして実行: **自分**
- アクセスできるユーザー: **全員**
- LINEに設定するURLは `/exec` で終わるWebアプリURL
- Apps Scriptを変更した後は **デプロイを管理 → 編集 → 新バージョン** で再デプロイ

WebアプリURLをブラウザで開いて `{"ok":true,...}` のように表示されれば、URL自体は有効です。

手動で編集する場合は、`schedule.csv` を以下の形式にします:

```csv
flight_iata,flight_date,scheduled_departure,scheduled_arrival,note
JL006,2026-05-10,2026-05-10T12:30:00+09:00,2026-05-11T11:00:00+05:00,NRT-JFK
JL005,2026-05-15,2026-05-15T11:00:00-04:00,2026-05-16T15:30:00+09:00,JFK-NRT
```

**カラム説明**

| カラム | 必須 | 例 | 説明 |
|--------|------|-----|------|
| `flight_iata` | ✅ | `JL006` | 便名(IATAコード) |
| `flight_date` | ✅ | `2026-05-10` | 運航日(出発地のローカル日付・YYYY-MM-DD) |
| `scheduled_departure` | ✅ | `2026-05-10T12:30:00+09:00` | 出発予定時刻(タイムゾーン付きISO8601) |
| `scheduled_arrival` | 推奨 | `2026-05-11T11:00:00+05:00` | 到着予定時刻(タイムゾーン付きISO8601) |
| `note` | 任意 | `NRT-JFK` | メモ用 |

編集したらコミット&プッシュすれば自動で反映されます。

### 7. 動作確認

リポジトリの **Actions** タブから **Flight Notifier** を選び、**Run workflow** で手動実行してみてください。

## 通知される内容

- 🛫 **出発** : `scheduled` → `active`
- 🛬 **到着** : `active` → `landed`
- ⏰ **大幅遅延** : 出発予定時刻に対し30分以上の遅延見込み(出発前1回のみ)
- ❌ **欠航** : ステータスが `cancelled`

## API使用量の目安

RapidAPI経由のAeroDataBox無料枠で運用する場合の目安:

- GitHub Actions: 5分間隔で起動
- API呼び出し: 同じ便は10分以上あけて実行
- 出発監視: 出発予定45分前 〜 出発予定2時間後
- 到着監視: 到着予定60分前 〜 到着予定90分後
- **長距離便1便あたり約25〜35リクエスト**
- **月2〜3便程度** が無料枠内の現実的な目安です

巡航中はAPIを叩かないため、5分間隔で起動しても長距離便の全時間を監視し続けるより大幅に節約できます。頻繁に使う場合は AeroDataBox の有料プラン、または監視対象便を必要な月だけ登録する運用を検討してください。

## トラブルシューティング

- **通知が来ない**: Actions タブでログを確認。`schedule.csv` の時刻フォーマット、Secrets の値を再確認
- **「データなし」が続く**: フライトが運航前か、AeroDataBoxのDBに存在しない便。`flight_date` が出発地のローカル日付になっているか確認
- **API使用量を節約したい**: `flight_notifier.py` の `MIN_API_INTERVAL_MIN` を大きくする、または監視ウィンドウを短くする
- **状態ファイルが肥大化**: `cleanup_old_state` が7日以上前のものを自動削除します

## 注意事項

- 「出発」= 離陸検知(プッシュバックではない)
- 「到着」= 着陸検知(ゲート到着ではない)
- AeroDataBoxの状態更新はデータソースの都合で遅延することがあります
- GitHub Actions の cron は負荷状況により数分ずれることがあります
- API呼び出しは10分以上あけるため、通知の遅延は **10〜15分程度** が目安です
