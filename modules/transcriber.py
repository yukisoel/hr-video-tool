import os
import subprocess
import tempfile
from openai import OpenAI
import config


_client = None
WHISPER_MAX_SIZE = 25 * 1024 * 1024  # 25MB


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _extract_text_from_result(result) -> str:
    segments = getattr(result, "segments", None) or []
    lines = []
    for seg in segments:
        text = getattr(seg, "text", None)
        if text is None and isinstance(seg, dict):
            text = seg.get("text")
        if text and text.strip():
            lines.append(text.strip())
    if lines:
        return "\n".join(lines)
    return getattr(result, "text", None) or ""


def _try_transcribe(file_path: str, language: str) -> tuple[str, str]:
    """(text, error_msg)を返す。成功時はtext=文字起こし、error_msg=空。"""
    try:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            result = _get_client().audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=(filename, f),
                language=language,
                response_format="verbose_json",
            )
        return _extract_text_from_result(result), ""
    except Exception as e:
        return "", f"Whisper API error: {e.__class__.__name__}: {str(e)[:200]}"


def transcribe(video_path: str, language: str = "ja") -> tuple[str, list[str]]:
    """動画を文字起こし。(transcript, log_messages) を返す。
    logs は Streamlitに表示するための診断メッセージ。
    """
    logs = []
    tmp_dir = tempfile.mkdtemp()

    # 診断: ファイルサイズを表示
    try:
        size_mb = os.path.getsize(video_path) / 1024 / 1024
        logs.append(f"📊 動画サイズ: {size_mb:.1f} MB")
    except OSError:
        pass

    # Try 1: 音声を強力に圧縮したmp3を作る（最も互換性高・小さいファイルサイズ）
    compressed_mp3 = os.path.join(tmp_dir, "audio.mp3")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", video_path,
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                compressed_mp3,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and os.path.exists(compressed_mp3) and os.path.getsize(compressed_mp3) > 0:
            logs.append(f"✅ 音声圧縮成功: {os.path.getsize(compressed_mp3) / 1024:.0f} KB")
            text, err = _try_transcribe(compressed_mp3, language)
            if text:
                return text, logs
            if err:
                logs.append(f"❌ {err}")
        else:
            logs.append(f"❌ ffmpeg mp3圧縮失敗 (code {result.returncode}): {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        logs.append("❌ ffmpeg タイムアウト (>120秒)")
    except Exception as e:
        logs.append(f"❌ ffmpeg 実行失敗: {e.__class__.__name__}: {str(e)[:200]}")

    # Try 2: 音声を acodec copy で抽出（再エンコードなし）
    copy_m4a = os.path.join(tmp_dir, "audio.m4a")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", video_path,
                "-vn", "-acodec", "copy",
                copy_m4a,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and os.path.exists(copy_m4a) and os.path.getsize(copy_m4a) > 0:
            logs.append(f"✅ 音声コピー成功: {os.path.getsize(copy_m4a) / 1024:.0f} KB")
            text, err = _try_transcribe(copy_m4a, language)
            if text:
                return text, logs
            if err:
                logs.append(f"❌ {err}")
        else:
            logs.append(f"❌ ffmpeg copy失敗 (code {result.returncode}): {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        logs.append("❌ ffmpeg copyタイムアウト")
    except Exception as e:
        logs.append(f"❌ ffmpeg copy実行失敗: {e.__class__.__name__}: {str(e)[:200]}")

    # Try 3: 動画を直接送信（25MB未満のみ）
    try:
        if os.path.getsize(video_path) < WHISPER_MAX_SIZE:
            logs.append("📤 動画を直接Whisperへ送信中…")
            text, err = _try_transcribe(video_path, language)
            if text:
                return text, logs
            if err:
                logs.append(f"❌ {err}")
        else:
            logs.append(f"⏭ 動画直接送信スキップ（25MB超過）")
    except Exception as e:
        logs.append(f"❌ 動画直接送信失敗: {e}")

    logs.append("⚠️ すべての試行が失敗しました")
    return "", logs
