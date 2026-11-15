# 親宛て郵便物の見える化 PoC（Daytona 版）

現地の人が親宛ての郵便物を LINE で撮って送ると、AI が内容を読み取り、離れて暮らす家族の LINE に「要対応／確認／捨ててよい」を付けて届けます。設計は [design.md](design.md) を参照してください。

> ハッカソン（Daytona HackSprint Tokyo 2026-11-15）用のデモです。LINE と AI はキーが無ければモックで動き、ブラウザの `/demo` で LINE のやりとりを再現できます。

## できること

- 写真の受け取り（続けて送った写真は1通にまとめる。既定は45秒以内）
- AI による読み取り（差出人・種類・要約・金額・期限・やること・重要度）＋安全側の補正
- 家族への通知と「対応済み／あとで／不要」ボタン
- 「捨ててよい」は毎日19時にまとめて通知（黙って消さない）
- 期限の7日前と前日に再通知（毎朝9時）
- 履歴ページ（URLを知っている人だけが見られる）
- 招待コードで撮影者と家族を追加
- `/demo`：3人分のスマホ画面を並べた LINE シミュレーター

## 動かす手順

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # DAYTONA_API_KEY を書き込む（LINE / AI は空ならモック）

# 手元で動かす
.venv\Scripts\python -m uvicorn app.main:app --reload   # http://localhost:8000/demo

# Daytona に配置（もう一度実行すると更新。data/ は残る）
.venv\Scripts\python deploy\daytona_deploy.py
.venv\Scripts\python deploy\daytona_deploy.py --logs
.venv\Scripts\python deploy\daytona_deploy.py --delete
```

最後に表示される `Demo URL` をブラウザで開けばデモができます。実際の LINE とつなぐときは、`Webhook URL` を LINE Developers コンソールの Messaging API 設定に登録して「Webhookの利用」をオンにし、公式アカウント側の「応答メッセージ」はオフにしてください。

| 環境変数 | 用途 | 空のとき |
|---|---|---|
| `DAYTONA_API_KEY` / `DAYTONA_API_URL` | 配置用（サンドボックスには渡さない） | 配置できない |
| `LINE_CHANNEL_SECRET` / `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API | `LINE_MODE=fake`（送信は記録のみ） |
| `ANTHROPIC_API_KEY` | Claude で写真を読み取る | `ANALYZER=fake`（用意した結果を返す） |

## デモの流れ（2分）

1. `/demo` を開き「① 家族をまとめて登録」を押す（子どもが `登録 母`、撮影者と兄弟が招待コードで `参加`）
2. 撮影する人の画面で読み取り結果を選び「📷 写真」を押す（何枚か続けて押すと1通にまとまる）
3. 45秒待つか「今すぐ読み取る」を押す → 子どもと兄弟に【要対応】の通知とボタンが届く
4. 兄弟が「対応済み」を押す → 子どもにも共有される
5. 「通販のDM」を送って「19時のまとめ通知」→ 捨ててよいものはまとめて届く
6. 「9時の期限通知」→ 期限が7日以内のものを再通知
7. 「履歴ページ ↗」で写真付きの一覧を見る

## LINE 上での使い方

1. 子どもが公式アカウントを友だち追加し、`登録 母` と送る → 招待コードが2つ届く
2. 撮影する人が友だち追加し、`参加 コード` と送る
3. 兄弟など通知を受け取る人も、もう一方のコードで `参加 コード` と送る
4. 撮影する人が郵便物の写真を送る → 家族に通知が届く
5. `一覧` と送ると未対応の郵便物と履歴ページのURLが返る

## テスト

```powershell
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest -q tests     # LINE と AI を偽物にしたテスト
```

## 中継プログラム（Nosana と LINE）

Daytona のティア1・2では、サンドボックスから外へ接続できる先が許可リストに限られ、`inference.nosana.com` にも `api.line.me` にも届きません。外からサンドボックスへの接続はできるので、ネットに出られる PC で中継プログラムを動かします。

```powershell
.venv\Scripts\python deploy\daytona_deploy.py    # 届かない先を調べ、中継方式で配置する（WORKER_TOKEN を .env に追加）
.venv\Scripts\python deploy\nosana_worker.py     # 動かし続ける
```

中継プログラムは1秒ごとにサンドボックスへ問い合わせ、次の3つを代行します。

- 読み取り待ちの写真を Nosana（`qwen/qwen3.8-27b`）で読み、結果を返す（1通8〜10秒）
- LINE への返信・通知を送る（アクセストークンはこの PC だけに置き、サンドボックスには渡さない）
- LINE で送られた写真をダウンロードして、アプリに渡す

中継が止まっていても、`REMOTE_TIMEOUT_SEC`（90秒）たつとモックで読み取るので、ブラウザのデモは止まりません（LINE への送信は中継が動くまで待ちになります）。デモ画面の上部に「Nosana（中継 接続中）」と出ていれば Nosana で読んでいます。

## Daytona で動かすときの注意

- サンドボックスは `public=True`（URLを知っていれば誰でもアクセス可）で作ります。Webhook は LINE の署名で、履歴ページは推測できないトークン付きURLで守っています。`/demo` は誰でも操作できるので、実運用では `DEMO_MODE=0` にしてください。
- 自動停止は無効にしています（`auto_stop_interval=0`）。動かしている間は Daytona の利用料がかかり続けます。
- サンドボックスを停止・再起動するとサーバーは止まります。もう一度 `daytona_deploy.py` を実行してください。データ（`data/`）は残ります。`--delete` すると消えます。
- Daytona はコード実行用の環境で、本番の常時運用向けではありません。数家族での試用までを想定しています。

## 構成

```text
app/config.py     設定
app/db.py         SQLite（family / member / mail_item / mail_image / notification_log）
app/line.py       LINE の署名検証と送受信（fake モードでは記録だけ）
app/analyzer.py   AI の読み取りと安全側の補正
app/messages.py   通知の文面
app/service.py    受付・まとめ・読み取り・通知・再通知
app/main.py       Webhook・履歴ページ・定期処理・デモ用API
app/static/       デモ画面・履歴ページ
deploy/daytona_deploy.py   Daytona への配置と起動
tests/test_poc.py
```

## まだやっていないこと

- 実際の LINE とつないだ確認（LINE は偽物でテスト。Nosana での読み取りは確認済み）
- 写真の暗号化、履歴ページのログイン、退会・データ削除の操作
- 設計書 6 章の法律・同意まわりの専門家確認
