import os
import subprocess
import tempfile
from openai import OpenAI
import config


_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _extract_audio(video_path: str) -> str:
    """Whisper送信用に音声だけをmp3で抽出（サイズ削減・25MB制限対策）。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
            tmp.name,
        ],
        check=True, capture_output=True,
    )
    return tmp.name


def transcribe(video_path: str, language: str = "ja") -> str:
    """動画を文字起こし。verbose_json でセグメントを取り、可読な改行付きテキストで返す。"""
    audio_path = _extract_audio(video_path)
    try:
        with open(audio_path, "rb") as f:
            result = _get_client().audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=f,
                language=language,
                response_format="verbose_json",
            )
        # セグメント別に改行
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
        # フォールバック：セグメントが空なら全文をそのまま返す
        full = getattr(result, "text", None) or ""
        return full
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass
