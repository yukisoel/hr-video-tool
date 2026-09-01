# フォーク先で独立して動かすためのセットアップ手順

このドキュメントは、本リポジトリを **フォークして自分の環境で（別用途で）Streamlit Cloud にデプロイ** したい人向け。

## 大方針

**APIキー・サービスアカウント鍵は元リポジトリ所有者から受け取らず、自分側で個別発行してください。**

理由：
- OpenAI / Anthropic のAPIキーは **使った分だけ元所有者に課金** される
- Googleサービスアカウント鍵は **元所有者のGoogle Drive/Docs/Slides への書き込み権限** を持つ
- 鍵のローテーションが利用者ごとに独立できる

受け取ってよいのは **テンプレSlidesの閲覧URL** のみ（コピーを作って自分のIDで使う）。

---

## 必要な Secrets 一覧

Streamlit Cloud の Settings → Secrets に貼り付ける項目：

| キー | 発行元 | 誰が用意する |
|-----|--------|-------------|
| `OPENAI_API_KEY` | platform.openai.com | フォーク側（自分） |
| `ANTHROPIC_API_KEY` | console.anthropic.com | フォーク側（自分） |
| `ANTHROPIC_MODEL` | 固定値 | サンプルからコピー |
| `SHARED_DRIVE_FOLDER_ID` | 自分のGoogle共有ドライブ | フォーク側（自分） |
| `TEMPLATE_SLIDES_ID` | 元リポジトリのテンプレを**コピー**して自分のID | フォーク側（自分） |
| `[google_service_account]` セクション | 自分のGCPで発行するcredentials.json | フォーク側（自分） |

フォーマットは [`.streamlit/secrets.toml.example`](../.streamlit/secrets.toml.example) を参照。

---

## セットアップ手順

### 1. OpenAI APIキー
1. <https://platform.openai.com/api-keys> にログイン
2. 「Create new secret key」で `sk-proj-...` を発行
3. Streamlit Secrets の `OPENAI_API_KEY` に貼る

### 2. Anthropic APIキー
1. <https://console.anthropic.com/settings/keys> にログイン
2. 「Create Key」で `sk-ant-api03-...` を発行
3. Streamlit Secrets の `ANTHROPIC_API_KEY` に貼る

### 3. Googleサービスアカウント（自分のGCPプロジェクトで）
1. <https://console.cloud.google.com/> でプロジェクトを作成（既存でもOK）
2. **APIとサービス → ライブラリ** で以下3つを「有効にする」
   - Google Drive API
   - Google Docs API
   - Google Slides API
3. **IAMと管理 → サービスアカウント → 作成**（名前は任意、ロール未指定でOK）
4. 作成後、そのサービスアカウントの **キー タブ → 鍵を追加 → JSON** をダウンロード
5. ダウンロードしたJSONの中身を Streamlit Secrets の `[google_service_account]` セクションに貼り付け
   （`private_key` は `"""..."""` の3連ダブルクォートで囲む — `.example` を参照）

### 4. 共有ドライブフォルダ（自分の共有ドライブに）
1. 自分の Google Workspace の **共有ドライブ** に、成果物保存用のフォルダを新規作成
2. そのフォルダをステップ3で作った **サービスアカウントのメールアドレス**（`xxx@yyy.iam.gserviceaccount.com`）に **コンテンツ管理者** 権限で共有
3. フォルダのURL `.../folders/【ここ】` の部分が `SHARED_DRIVE_FOLDER_ID`

> 個人Driveではなく **共有ドライブ** が必須。個人Driveだとサービスアカウントの容量制限に引っかかる。

### 5. テンプレSlides
1. 元リポジトリ所有者から **テンプレSlidesの閲覧URL** をもらう
2. Slidesを開き **ファイル → コピーを作成** で自分のドライブ配下にコピー
3. コピーしたSlidesを ステップ3の **サービスアカウントのメールアドレスに 編集者 権限で共有**
4. コピーしたSlidesのURL `.../presentation/d/【ここ】/edit` の部分が `TEMPLATE_SLIDES_ID`

> テンプレSlides内の `{{タイトル}}` `{{フック1}}` などのプレースホルダー仕様は README §2 と `docs/design.md` §4-2 を参照。

### 6. Streamlit Cloud にデプロイ
1. GitHubにフォークをpushしておく
2. <https://share.streamlit.io/> で **New app** → フォークしたリポジトリを選択
3. **Main file path** は `app.py`
4. **Advanced settings → Secrets** に上記1〜5で作った値を貼り付け
5. Deploy

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `Forbidden` / `Access Denied` | 共有ドライブフォルダとテンプレSlidesが **サービスアカウントのメール** に共有されているか再確認 |
| `credentials.json が見つかりません` | Streamlit Cloud では `.streamlit/secrets.toml` から自動読み込みされる。ローカルテストする場合は `credentials.json` を配置 |
| Slidesの `{{フック1}}` が置換されない | オートコレクトで `{{` `}}` が分割されている可能性。プレーンテキストで再貼り付け |
| Whisper `413 Request Entity Too Large` | 25MB制限。長尺動画は `modules/transcriber.py` の音声ビットレート `-b:a 64k` を下げる |

---

## セキュリティメモ

- `.env` / `credentials.json` / `.streamlit/secrets.toml` は `.gitignore` 済み。**コミットしない**
- Streamlit Secretsは各Deploy先で個別管理。**チャットや口頭で共有しない**
- APIキーが漏れたら即 `Revoke` して再発行
