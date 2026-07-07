# HR動画分析ツール

TikTok / Instagram の投稿URLを入れると、共有Google Driveに以下を一括生成するローカルStreamlitアプリ。

- 参考動画◯◯用のフォルダ
- 動画本体（mp4）
- 文字起こし（Google Docs）
- 分析（Google Docs）
- 参考動画スライド（Google Slides、既存テンプレを差し込み）

すべてリンクを知る全員に閲覧権限を自動付与します。

---

## 1. 事前準備

### 1-1. Homebrew系ツール（macOS）
```bash
brew install ffmpeg python@3.11
```

### 1-2. Pythonパッケージ
```bash
cd /Users/takedayuuki/Desktop/hrbunseki-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1-3. APIキー取得
- **OpenAI**: <https://platform.openai.com/api-keys> で `sk-...` を発行（Whisper用途）。
- **Anthropic**: <https://console.anthropic.com/settings/keys> で `sk-ant-...` を発行（Claude用途）。

### 1-4. Googleサービスアカウント作成
1. <https://console.cloud.google.com/> でプロジェクトを1つ用意（既存でOK）。
2. **APIとサービス → ライブラリ** で下記3つを「有効にする」：
   - Google Drive API
   - Google Docs API
   - Google Slides API
3. **IAMと管理 → サービスアカウント → 作成**。名前は `hr-video-tool` などでOK。ロールは未指定でよい。
4. 作成後、そのサービスアカウントの **キー タブ → 鍵を追加 → JSON** をダウンロード。
5. ダウンロードしたJSONを `hrbunseki-tool/credentials.json` に配置。

### 1-5. 共有ドライブとテンプレの共有
サービスアカウントは「別のGoogleアカウント」として扱われるため、**共有ドライブとテンプレSlidesをサービスアカウントのメールアドレスに共有**する必要があります。

1. Google Driveで保存先の**共有ドライブ配下フォルダ**を開き、`credentials.json` の中の `client_email`（例: `hr-video-tool@xxx.iam.gserviceaccount.com`）を **コンテンツ管理者** 権限で追加。
2. **テンプレSlides** も同じサービスアカウントに **編集者** 権限で共有。

### 1-6. 各種IDを取得
- **保存先フォルダID**：フォルダを開いたURLの `.../folders/【ここ】` の部分。
- **テンプレSlidesのID**：スライドを開いたURLの `.../presentation/d/【ここ】/edit` の部分。

### 1-7. .env作成
```bash
cp .env.example .env
```
`.env` を開き、上で取得したキーとIDを埋める。

---

## 2. テンプレSlidesの作り方

既存テンプレの本文テキストボックスに、以下の**プレースホルダー**を入れておくと自動置換されます。

| プレースホルダー | 埋め込まれる内容 |
|---|---|
| `{{No}}` | 参考動画番号（UI入力）|
| `{{タイトル}}` | Claudeが決めたサブタイトル |
| `{{URL}}` | 元動画URL |
| `{{プラットフォーム}}` | TikTok / Instagram Reels 等 |
| `{{アカウント名}}` | uploader / @handle |
| `{{尺}}` | 約XX秒 |
| `{{投稿日}}` | YYYYMMDD |
| `{{フック1}}` | フック1項目目（45文字以内） |
| `{{フック2}}` | フック2項目目 |
| `{{構成メモ1}}`〜`{{構成メモ3}}` | 構成メモ 3項目 |
| `{{転用ポイント1}}`〜`{{転用ポイント3}}` | 転用ポイント 3項目 |

> **注意**：Slidesのテキストボックス内で `{{` と `}}` が **1つの文字列として連続して入力されている必要があります**。オートコレクトで分割されないように、貼り付け後に一度クリックして確認してください。

---

## 3. 起動

```bash
cd /Users/takedayuuki/Desktop/hrbunseki-tool
source .venv/bin/activate
streamlit run app.py
```

ブラウザで `http://localhost:8501` が開きます。

---

## 4. 使い方

1. TikTok / Instagram の投稿URLを貼り付ける。
2. 「参考動画番号」に例：`70` などを入力（省略可）。
3. **実行**を押す。

進捗が可視化されつつ、完了後にDriveフォルダのリンクが表示されます。

---

## 5. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `credentials.json が見つかりません` | パスを確認。`.env` の `GOOGLE_SERVICE_ACCOUNT_JSON` と実ファイル位置を一致させる。 |
| `Forbidden` / `Access Denied` | サービスアカウントに共有ドライブとテンプレSlidesの権限が付与されているか再確認。 |
| Whisperが `413 Request Entity Too Large` | Whisperは25MB制限。`transcriber.py` で音声抽出時のビットレート `-b:a 64k` を下げる。 |
| yt-dlpでIGがログイン要求 | 非公開投稿は取得できません。公開投稿のみ対応。 |
| Slidesの `{{フック1}}` が置換されない | オートコレクトで文字が分割されている可能性。プレーンテキストで再貼り付け。 |

---

## 6. 構成

```
hrbunseki-tool/
├── app.py                 # Streamlit UI
├── config.py              # .env読み込み・設定検証
├── requirements.txt
├── .env.example
├── credentials.json       # サービスアカウント鍵（要配置・要gitignore）
└── modules/
    ├── downloader.py      # yt-dlp
    ├── transcriber.py     # Whisper（ffmpegで音声抽出）
    ├── analyzer.py        # Claude（tool useで構造化出力）
    ├── google_client.py   # サービスアカウント認証
    ├── drive.py           # フォルダ作成・UP・権限
    ├── docs.py            # Docs作成＋テキスト挿入
    └── slides.py          # テンプレコピー＋プレースホルダー置換
```

---

## 7. 想定コスト（1本あたり目安）

- **Whisper**: `$0.006 / 分` × 動画尺 ≒ **1本1〜5円**
- **Claude Opus 4.7**: 入出力トータル数千トークン ≒ **1本10〜30円**
- 合計：**1本あたり おおよそ15〜35円**

---

## 8. 拡張候補（メモ）

- 複数URL一括投入
- 過去分析の一覧化（Drive内フォルダをリストで表示）
- 元動画の`{{尺・投稿日}}`欄を「約XX秒 / YYYY年MM月DD日」形式で整形
- SlackやChatwork通知連携
