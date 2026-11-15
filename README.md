# 親宛て郵便物の見える化 PoC（Daytona 版）

現地の人が親宛ての郵便物を LINE で撮って送ると、AI が内容を読み取り、離れて暮らす家族の LINE に「要対応／確認／捨ててよい」を付けて届けます。設計は [design.md](design.md) を参照してください。

> Daytona HackSprint Tokyo（2026-11-15）で作りました。アプリは **Daytona サンドボックス**で動き、郵便物の写真は **Nosana** 上のモデルが読み取ります。

**In English:** A helper photographs an elderly parent's mail and sends it on LINE. A vision model running on **Nosana** reads each letter (sender, amount, due date, what to do) and flags suspected scams. Family members living far away get a LINE notification labeled *action needed / check / safe to discard*, with one-tap status buttons shared across the family. The whole app runs in a **Daytona sandbox**. It works end to end today: a live LINE official account, real model inference on Nosana, 10 passing tests.

## 審査基準ごとの要点

| 審査基準 | 一言で | 確かめられる場所 |
|---|---|---|
| Innovation | 読めない郵便を、家族で分担できる用件に変える。詐欺の疑いも警告する | [app/analyzer.py](app/analyzer.py) の `correct`、[app/service.py](app/service.py) |
| Completeness | 登録から通知・共有・再通知・履歴まで通しで動く。テスト10件 | [tests/test_poc.py](tests/test_poc.py)、`/demo` |
| Real-Life Problem Solving | 離れて暮らす親の郵便を、期限が切れる前に家族が把握できる | 下の「課題解決」、[design.md](design.md) |
| Sponsored Product Usage | アプリ全体が Daytona で動き、すべての写真を Nosana が読む | [deploy/daytona_deploy.py](deploy/daytona_deploy.py)、[deploy/nosana_worker.py](deploy/nosana_worker.py) |

### Innovation（革新性）

- **「読めない郵便」を、家族で分担できる用件に変えます。** 写真を共有するだけの見守りとは違い、AI が1通ごとに差出人・金額・期限・やることを取り出し、要対応／確認／捨ててよいに仕分けます。家族は写真を読み解く必要がありません。
- **架空請求や詐欺の疑いを、親が動く前に家族へ警告します。** 「訴訟」「最終通告」で不安をあおる、本日中の電話を迫る、プリペイドカードで払わせる、といった特徴を AI が見つけると、理由を添えて「⚠️ 詐欺の疑い」として通知します。やることは「支払いも電話もせず、家族で確認する」に置き換え、期限の再通知（支払いを促す通知）は送りません。
- **新しいアプリを入れません。** 撮る人も受け取る人も、普段使っている LINE だけで完結します。高齢の親やヘルパーに操作を覚えてもらう必要がありません。
- **AI を信用しすぎない設計です。** 役所・税・年金・保険などの言葉、金額、期限がある郵便は、AI が「捨ててよい」と答えても「確認」に引き上げます。読み取りに失敗したら、推測せず写真を家族に見てもらいます。「捨ててよい」も黙って消さず、毎日19時にまとめて知らせます。
- **サンドボックスの通信制限を、設計で越えました。** Daytona のティア1・2では、サンドボックスから Nosana にも LINE にも接続できません。外から中へは接続できることを使い、外の中継プログラムが仕事を取りに行く方式（pull 型）にしました。アプリ本体は隔離された環境に置いたまま、外部の AI とメッセージ基盤を使えます。LINE のアクセストークンをサンドボックスに置かずに済む利点もあります。

### Completeness（完成度）

最低限の試作ではなく、実際の利用の流れが最初から最後まで動きます。

| 機能 | 状態 |
|---|---|
| 招待コードで家族と撮影者を登録 | 実際の LINE 公式アカウントで確認済み |
| 続けて送った写真を1通にまとめる（45秒） | 動作・テスト済み |
| Nosana のモデルで写真を読み取り、仕分ける | 公開URLで確認済み（通知まで8〜15秒） |
| 詐欺・架空請求の疑いを警告する | 公開URLで確認済み・テスト済み |
| 家族全員に通知、「対応済み／あとで／不要」を共有 | 動作・テスト済み |
| 「捨ててよい」の19時まとめ通知 | 動作・テスト済み |
| 期限の7日前・前日の再通知 | 動作・テスト済み |
| 写真付きの履歴ページ | 動作済み |
| ブラウザで3人分の LINE を再現するデモ画面 | 動作済み |
| LINE への送信と写真の取得を中継する | 送信は実際の LINE で確認済み。写真の取得はテスト済み |
| 中継が止まったときの自動切り替え（90秒でモック） | テスト済み |

- 自動テストは10件あり、すべて通ります（`pytest -q tests`）。偽の LINE と偽の AI を使うので、キーなしで実行できます。
- 配置はコマンド1つです（`python deploy/daytona_deploy.py`）。もう一度実行すれば、データを残したまま更新できます。データベースの項目追加も自動で移行します。
- キーが無い環境でもモックで動くので、誰でも手元で試せます。
- デモ画面のリセットは、デモ用のデータだけを消します。実際の LINE で登録した家族は残ります。
- まだやっていないことは、この README の最後に書いています。

### Real-Life Problem Solving（課題解決）

- **誰の困りごとか。** 親と離れて暮らす子ども世代です。実家には税金の納付書、保険の更新、病院からの知らせが届きますが、親は中身を判断しにくく、子どもは封筒の存在すら知りません。気づいたときには期限が過ぎています。
- **なぜ今の方法では足りないか。**
  - 電話で「何か届いてない？」と聞いても、親は説明できません。
  - 帰省して郵便の山を開けるのでは遅すぎます。
  - 写真を家族のグループに送ってもらう方法は、読む人が決まらず、誰が対応したかも残りません。
- **このアプリが変えること。**
  - 撮る人は写真を送るだけです（判断はしません）。
  - 家族には、やることと期限が書かれた通知が届きます。
  - 誰かが「対応済み」を押せば全員に伝わるので、二重払いや押し付け合いが起きません。
  - 期限の7日前と前日に再通知するので、「あとで」と思って忘れることを防げます。
  - 架空請求のハガキは、親が電話をかける前に家族へ警告が届きます。
- **続けられる費用です。** 1通の読み取りは、入力約1,360トークン・出力約140〜200トークンです。Nosana の利用記録では1通あたり約0.0006〜0.0007ドル（0.1円ほど）でした。
- **すぐ始められます。** 必要なのは LINE の友だち追加と、`登録 母`・`参加 コード` の2つの言葉だけです。
- 個人情報を扱うので、法律・同意の論点は [design.md](design.md) の6章にまとめ、専門家の確認が必要な点として明記しています。

### Sponsored Product Usage（スポンサー製品の活用）

2社の製品を、付け足しではなく中心の部品として使っています。どちらかを外すとアプリは動きません。

**Daytona — アプリ全体の実行環境**

サーバー（FastAPI）・データベース（SQLite）・写真の保存・定期処理（まとめ通知、期限の再通知）のすべてが、Daytona サンドボックス1台の中で動きます。配置は Daytona の Python SDK で自動化しています。

| 使っている Daytona の機能 | 用途 | 場所 |
|---|---|---|
| `daytona.create()`（ラベル・公開・自動停止なし） | アプリ用のサンドボックスを作る | [deploy/daytona_deploy.py](deploy/daytona_deploy.py) の `main` |
| `daytona.list()` とラベル | 既存のサンドボックスを探して使い回す（再配置してもデータが残る） | 同 `find` |
| `sandbox.fs.upload_file()` | コードと設定を送る | 同 `upload` |
| `sandbox.process.exec()` | 依存パッケージを入れる。届く通信先を調べる | 同 `upload`、`main` |
| `sandbox.process` のセッション | サーバーを常駐させ、ログを取る | 同 `start`、`logs` |
| `sandbox.get_preview_link()` | 公開URLを取得し、LINE の Webhook とデモ画面に使う | 同 `main` |

配置スクリプトは、サンドボックスから Nosana と LINE に届くかを実際に調べ、届かなければ中継方式に自動で切り替えます。Daytona の通信制限（ティア1・2の許可リスト）を調べたうえで組み立てた構成です。

**Nosana — 郵便物を読む AI**

| 使っている Nosana の機能 | 用途 | 場所 |
|---|---|---|
| 推論 API（OpenAI 互換、`qwen/qwen3.8-27b`） | すべての郵便物の写真を読み取る | [app/analyzer.py](app/analyzer.py) の `analyze_nosana` |
| 画像入力（複数枚） | 表と裏など、まとめた写真を1回で読む | 同上 |
| 構造化出力（JSON スキーマ指定） | 差出人・金額・期限・やること・重要度・詐欺の疑いを決まった形で受け取る | 同 `SCHEMA` |
| 思考機能の切り替え | 読み取りを約16秒から約5秒に短縮（結果は同じ） | 同 `analyze_nosana`、`NOSANA_THINKING` |
| 並行リクエスト | 複数の郵便を同時に読む | [deploy/nosana_worker.py](deploy/nosana_worker.py) |

日本語の納付書・DM・健診結果・架空請求ハガキのサンプル（[app/static/samples/](app/static/samples/)）で、金額・期限・仕分け・詐欺の判定が正しいことを確認しました。

```text
[LINE / ブラウザ] ──▶ [Daytona サンドボックス: アプリ本体・DB・写真・定期処理]
                              ▲ 読み取り待ち・送信待ちを取りに行く／結果を返す
                       [中継プログラム] ──▶ [Nosana: qwen3.8-27b が写真を読む]
                              └────────▶ [LINE Messaging API: 返信・通知・写真の取得]
```

## できること

- 写真の受け取り（続けて送った写真は1通にまとめる。既定は45秒以内）
- AI による読み取り（差出人・種類・要約・金額・期限・やること・重要度）＋安全側の補正
- 架空請求・詐欺の疑いがある郵便を、理由付きで警告
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
   - 「架空請求のハガキ」を送ると、「⚠️ 詐欺の疑い」の警告が理由付きで届く
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

- 実際の LINE での写真送信から通知までの通し確認（登録と返信は実際の LINE で確認済み。写真以降はデモ画面と自動テストで確認）
- 写真の暗号化、履歴ページのログイン、退会・データ削除の操作
- 設計書 6 章の法律・同意まわりの専門家確認
