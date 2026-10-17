# Meeting Translator – GitHub / Windows 配布版（試験実装）

Zoom / Teams / Google Meet へBotを追加せず、各自のPCで会議音声を取得し、日英の字幕を表示するアプリです。相手も同じEXEを使用する場合、**各自が相手の音声を自分のPCで文字起こし・翻訳**します。字幕同期サーバーは使いません。日本語の音声を英語の音声に変換して相手へ送信する機能はありません。

## 構成

- Windows 11: `soundcard` によるスピーカーループバック + マイク入力
- 音声認識: Amazon Transcribe Streaming（推奨）/ OpenAI 5秒チャンク（オプション、APIキーが必要）
- 翻訳: Amazon Bedrock Converse API、Claude Haiku 5.5（環境変数 `BEDROCK_MODEL_ID` で変更可能）
- 認証: Cognito User Poolでブラウザログイン（OAuth 2.0 Authorization Code + PKCE）、Identity Poolで一時AWS認証情報を取得。AWSアカウントを相手へ配布する必要なし
- 旧方式: AWSキー3項目を直接入力、または既存AWS CLIプロファイルも利用可能
- クラウドへ音声と文字起こしデータが送信されます。参加者への録音・処理の通知と組織の規程確認が必要です。

## 管理者：AWS構築（初回のみ）

実行前に `aws` CLI / 権限（IAM,Cognito,CloudFormation）がある管理者が操作します。

```powershell
aws cloudformation deploy --template-file aws/cognito.yaml `
  --stack-name meeting-translator-auth `
  --region ap-northeast-1 `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides DomainPrefix=YOUR-UNIQUE-PREFIX

aws cloudformation describe-stacks --stack-name meeting-translator-auth `
  --region ap-northeast-1 --query 'Stacks[0].Outputs'
```

得られた出力値を `config.example.json` の各フィールドに反映し、**`config.json`として保存**します。`config.json`にはパスワード・シークレットは含まれません（公開クライアントIDなどのみ）。Git管理はしない設定です。

アカウントを提供する各相手には、Cognito User Poolへユーザーを作成して案内します。

```powershell
aws cognito-idp admin-create-user --user-pool-id YOUR_USER_POOL_ID `
  --username example@example.com --user-attributes Name=email,Value=example@example.com `
  --region ap-northeast-1
```

※ `BedrockModelResourceArn` の既定 `*` はPOC向けです。本番配布では対象推論プロファイルおよび呼び出し先モデルのARNに絞り、利用者数・予算・レート制限を設定してください。モデルの利用規約・利用可能リージョン・クロスリージョン推論を確認してください。

## 利用者：インストール / 起動

1. GitHub Releasesから`MeetingTranslator-Setup-<version>.exe`を取得
2. 管理者から受け取った `config.json` を **セットアップEXEと同じフォルダ**に置いてセットアップを実行（自動でインストール先へコピーされます。手入力方式では不要）。管理者権限は不要で、`%LOCALAPPDATA%\Programs\Meeting Translator` にインストールされます
3. スタートメニューの「Meeting Translator」から起動

※ インストールせずに使う場合は`MeetingTranslator-Windows.zip`を解凍し、`config.json`を`MeetingTranslator.exe`と同じフォルダに置いて起動します。
4. 「ブラウザでAWSログイン」を押し、各自のCognitoユーザーでログイン
5. スピーカーとマイクを指定。音声認識は「Amazon Transcribe Streaming」を選ぶ
6. 「双方向モード（双方のPCで実行）」を選び、両者がそれぞれ自分のPCで翻訳開始。相手の発言が自分の画面で翻訳されます。

**重要:** 双方向モードは字幕表示の使い方を示すモードです。PC間のピア接続や字幕転送は行いません。双方がZoom等の会議で声を聞けることが前提です。

### ソースコードをWindowsで起動

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

従来どおり、AWSの一時認証情報3項目を画面に直接入力する方式、またはAWS CLIプロファイルも使えます。OpenAI認識時は別途 `OPENAI_API_KEY` が必要です。

## GitHubへのpush / EXE配布

`.github/workflows/windows-release.yml`がテスト・PyInstallerビルド・`installer/MeetingTranslator.iss`によるセットアップEXE作成を行います。

- GitHub Actions → `Windows release` → `Run workflow` でセットアップEXEとZIPをActions Artifactsへ生成
- `v0.1.0`などのGitタグをpushするとGitHub Releaseに`MeetingTranslator-Setup-<version>.exe`（Inno Setup製）と`MeetingTranslator-Windows.zip`を添付
- Python実行環境不要の`--onedir`配布。**未署名のためWindows SmartScreen等の警告が出る場合があります**。公開配布前にはコード署名と実機動作検証を推奨します

```powershell
git add .
git commit -m "Add Cognito authentication and Windows release build"
git push origin main
git tag v0.1.0
git push origin v0.1.0
```

## 制約・セキュリティ

- **未実施：Windows実機での音声取得、Cognito認証の実API試験、CloudFormationデプロイ、GitHub Actionsビルド、Bedrock/Transcribeの接続テスト。** ソース静的検査と最小単体テストのみ済みです。
- Cognito取得の一時AWS認証情報は現在メモリに保持し、自動更新しません。Identity Pool発行認証情報は通常約1時間で失効するため、失効時はアプリを止め、再ログインしてください。
- Cognito経由でも一時AWS認証情報は**利用者端末へ渡ります**。ユーザーが同じIAM権限で直接AWS APIを呼べる点に注意。利用者にAWSキーを渡さない**完全なサーバー代理方式**ではありません。利用量・権限境界・濫用対策を本番前に設計してください。
- 配布EXEにAWSの秘密鍵は同梱しないでください。`config.json`は公開設定のみです。
- 既存マイク音声が会議アプリへ送られたままであり、音声翻訳の吹き替えはしません。
- 相手への字幕共有は不要な構成ですが、双方が各自の会議音声を受信できる必要があります。
- `http://localhost:8765/callback` は開発・検証向けループバックコールバックです。企業向け本番配布ではデスクトップOAuthリダイレクト方式を再評価してください。
- `Ctrl`を押しただけで画面の字幕は相手へ送信されません。

## トラブルシュート

- `temperature is deprecated`: 最新版では `inferenceConfig={'maxTokens':512}` に変更済みです。
- 認証ブラウザが戻らない: `localhost:8765` で別プロセスが待ち受けていないか確認してください。
- `AccessDeniedException`: Cognito Identity Pool認証ロールのBedrock / Transcribe権限、モデル利用可否、推論プロファイルのリージョンを確認してください。
- Windowsの録音ができない: 録音デバイス、会議音声の出力先、ループバック対応、OSのマイク権限を確認してください。
