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


def _has_audio_stream(video_path: str) -> bool:
    """ffprobeで音声ストリームの有無を確認。"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True, text=True, check=True,
        )
        return "audio" in result.stdout
    except subprocess.CalledProcessError:
        return False


def _extract_audio(video_path: str) -> str | None:
    """音声抽出。失敗したらフォールバック順に試行し、全滅なら None。"""
    tmp_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name

    # 音声ストリームなしなら即諦める
    if not _has_audio_stream(video_path):
        return None

    # Try 1: 標準（16kHz mono mp3 64kbps）
    cmds = [
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
            tmp_mp3,
        ],
        # Try 2: ビットレート指定なし
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000",
            tmp_mp3,
        ],
        # Try 3: 最小限のオプション
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", tmp_mp3,
        ],
    ]

    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            if os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                return tmp_mp3
        except subprocess.CalledProcessError:
            continue

    return None


def transcribe(video_path: str, language: str = "ja") -> str:
    """動画を文字起こし。音声なし/抽出失敗時は空文字を返す（分析は継続可）。"""
    audio_path = _extract_audio(video_path)
    if audio_path is None:
        return ""

    try:
        with open(audio_path, "rb") as f:
            result = _get_client().audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=f,
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
        # Whisper API側で失敗した場合も空文字返却で分析継続
        return ""
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass
