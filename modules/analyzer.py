import base64

import anthropic

import config


_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


CLASSIFICATION_OPTIONS = [
    "若手社員の1日密着",
    "入社理由・就活ストーリー",
    "キャリアパス可視化",
    "内定者・同期の雰囲気",
    "経営者・先輩の本音Q&A",
    "理念・ブランドストーリー",
]

TONE_OPTIONS = [
    "エンタメ・ネタ系",
    "バラエティ・キャラ系",
    "ドキュメント・リアル系",
    "対話・トーク系",
    "エモ・シネマ系",
    "情報整理・カード系",
]


SCHEMA = {
    "type": "object",
    "properties": {
        "video_title": {
            "type": "string",
            "description": "動画の内容を簡潔に要約したタイトル。20文字以内厳守。企業名・職種・切り口・フォーマットなど動画の“何が特徴か”が伝わる短いフレーズ。",
            "maxLength": 20,
        },
        "classification": {
            "type": "string",
            "description": "動画の類型。以下6択から最も当てはまるものを1つだけ選ぶ。",
            "enum": CLASSIFICATION_OPTIONS,
        },
        "tone": {
            "type": "string",
            "description": "動画のトーン。以下6択から最も当てはまるものを1つだけ選ぶ。",
            "enum": TONE_OPTIONS,
        },
        "hook": {
            "type": "array",
            "description": "フック（冒頭2秒で何が起きるか）。ちょうど2項目。各45文字以内。",
            "minItems": 2, "maxItems": 2,
            "items": {"type": "string", "maxLength": 45},
        },
        "structure": {
            "type": "array",
            "description": "構成メモ（本編の展開・編集の特徴）。ちょうど3項目。各45文字以内。テロップ・カット割り・演出など視覚要素も対象。",
            "minItems": 3, "maxItems": 3,
            "items": {"type": "string", "maxLength": 45},
        },
        "adaptation": {
            "type": "array",
            "description": "転用ポイント（貴社版で真似る要素）。ちょうど3項目。各45文字以内。",
            "minItems": 3, "maxItems": 3,
            "items": {"type": "string", "maxLength": 45},
        },
        "summary_title": {
            "type": "string",
            "description": "資料に載せるサブタイトル（1行）。企業名や題材が分かるように。",
        },
    },
    "required": [
        "video_title", "classification", "tone",
        "hook", "structure", "adaptation", "summary_title",
    ],
}


SYSTEM = """あなたは新卒採用SNS動画（TikTok/Reels/Shorts）のクリエイティブ分析家です。
渡された「文字起こし（Whisperによる音声認識）」と「動画のキーフレーム画像」を総合して日本語で分析してください。

【厳守】
- video_title は 20 文字以内。動画の“何が特徴か”を短く。
- classification は 6 択から 1 つ選ぶ：若手社員の1日密着 / 入社理由・就活ストーリー / キャリアパス可視化 / 内定者・同期の雰囲気 / 経営者・先輩の本音Q&A / 理念・ブランドストーリー。
- tone は 6 択から 1 つ選ぶ：エンタメ・ネタ系 / バラエティ・キャラ系 / ドキュメント・リアル系 / 対話・トーク系 / エモ・シネマ系 / 情報整理・カード系。
- hook は 2 項目、structure は 3 項目、adaptation は 3 項目。各 45 文字以内。
- 資料スライドの点線枠に貼り付ける短文。体言止めまたは断定形で密度高く。
- 意味の重複を避ける。1項目1論点。
- 「頑張ります」等の抽象語は禁止。ロケ・演出・数字・行動・視覚要素など固有の観察を書く。
- 画像に写るテロップ・表情・カット割り・場面転換など視覚情報を必ず活用する。
"""


def analyze_video(transcript: str, frames: list[str], meta: dict) -> dict:
    """文字起こし+キーフレーム画像を Claude に送って構造化分析。"""
    client = _get_client()

    # 画像コンテンツを構築
    content_blocks = []
    for path in frames:
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        })

    user_msg_text = f"""# 動画メタ
- タイトル/説明: {meta.get('title', '')}
- 投稿者: {meta.get('uploader', '')} / @{meta.get('uploader_id', '')}
- 尺(秒): {meta.get('duration')}
- 投稿日: {meta.get('upload_date')}
- URL: {meta.get('webpage_url')}
- プラットフォーム: {meta.get('extractor')}

# 文字起こし（Whisperによる音声認識）
{transcript}

上記の文字起こしと、添付キーフレーム画像({len(frames)}枚・動画の時系列を等間隔サンプリング)を総合して、
指定スキーマ通りに分析結果を output ツール経由で返してください。"""

    content_blocks.append({"type": "text", "text": user_msg_text})

    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content_blocks}],
        tools=[{
            "name": "output",
            "description": "分析結果を構造化して返す",
            "input_schema": SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "output"},
    )

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "output":
            return block.input
    raise RuntimeError("Claudeが構造化出力を返しませんでした")


# 後方互換
def analyze(transcript: str, meta: dict) -> dict:  # deprecated
    return analyze_video(transcript, [], meta)
