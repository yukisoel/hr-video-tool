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


def _extract_audio(video_path: str) -> str | None:
    """音声抽出。複数フォールバックあり。失敗時は None。"""
    tmp_dir = tempfile.mkdtemp()

    attempts = [
        # Try 1: 音声ストリームをそのまま copy（再エンコードなし・最速）
        (
            os.path.join(tmp_dir, "audio.m4a"),
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-acodec", "copy",
            ],
        ),
        # Try 2: mp3 に再エンコード（16kHz mono 64kbps）
        (
            os.path.join(tmp_dir, "audio.mp3"),
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
            ],
        ),
        # Try 3: WAV に再エンコード（ビットレート指定なし、最も互換性高）
        (
            os.path.join(tmp_dir, "audio.wav"),
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-ac", "1", "-ar", "16000",
            ],
        ),
        # Try 4: 最小オプション（拡張子自動判別）
        (
            os.path.join(tmp_dir, "audio.mp3"),
            [
                "ffmpeg", "-y", "-i", video_path, "-vn",
            ],
        ),
    ]

    for out_path, cmd in attempts:
        try:
            subprocess.run(cmd + [out_path], check=True, capture_output=True)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except subprocess.CalledProcessError:
            continue

    return None


def transcribe(video_path: str, language: str = "ja") -> str:
    """動画を文字起こし。音声なし/抽出失敗時は空文字を返す（分析は継続可）。"""
    audio_path = _extract_audio(video_path)
    if audio_path is None:
        return ""

    try:
        filename = os.path.basename(audio_path)
        with open(audio_path, "rb") as f:
            result = _get_client().audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=(filename, f),
                language=language,
                response_format="verbose_json",
            )
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
        full = getattr(result, "text", None) or ""
        return full
    except Exception:
        return ""
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass
