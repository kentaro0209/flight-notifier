# 妻のフライト出発・到着通知システム

GitHub Actionsで5分おきにフライト状態を監視し、出発・到着・大幅遅延・欠航時にLINEへ通知します。

## 構成

- **データ取得**: AviationStack API (無料枠 月500リクエスト)
- **通知**: LINE Messaging API (無料枠 月200通)
- **実行基盤**: GitHub Actions (パブリックリポジトリなら無制限)
- **状態管理**: `flight_state.json` をリポジトリに自動コミット

## セットアップ手順

### 1. AviationStack APIキーを取得

1. <https://aviationstack.com/signup/free> でサインアップ
2. ダッシュボードから API Access Key をコピー

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
| `AVIATIONSTACK_KEY` | AviationStackのAPIキー |
| `LINE_CHANNEL_TOKEN` | LINEのChannel access token |
| `LINE_USER_ID` | LINEのユーザーID(`U`から始まる文字列) |

### 5. ワークフロー権限の設定

**Settings → Actions → General → Workflow permissions** で以下を選択:

- ✅ **Read and write permissions**

これで Actions が `flight_state.json` をリポジトリにコミットできます。

### 6. スケジュール登録

`schedule.csv` を編集して、奥様のフライト予定を入れます:

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

週3便×2区間=月約26便で運用した場合の試算:

- 監視ウィンドウ: 出発予定の30分前 〜 到着予定の3時間後
- 5分間隔で監視、状態確定で停止
- **1便あたり平均10〜15リクエスト**
- **月合計: 約300〜400リクエスト** (無料枠500の範囲内)

## トラブルシューティング

- **通知が来ない**: Actions タブでログを確認。`schedule.csv` の時刻フォーマット、Secrets の値を再確認
- **「データなし」が続く**: フライトが運航前か、AviationStackのDBに存在しない便。`flight_date` が出発地のローカル日付になっているか確認
- **API使用量を節約したい**: ワークフローの cron を `*/10 * * * *`(10分間隔)に変更
- **状態ファイルが肥大化**: `cleanup_old_state` が7日以上前のものを自動削除します

## 注意事項

- 「出発」= 離陸検知(プッシュバックではない)
- 「到着」= 着陸検知(ゲート到着ではない)
- AviationStackは状態更新が30〜60秒程度遅延します
- 5分間隔のcronは GitHub Actions の負荷状況により数分ずれることがあります
- 結果として通知の遅延は **5〜10分程度** が目安です
