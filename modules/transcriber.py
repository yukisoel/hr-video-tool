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
    """Whisperのverbose_jsonからテキストを取り出す。セグメント単位で改行。"""
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


def _extract_audio(video_path: str) -> str | None:
    """音声抽出（フォールバック用）。失敗時は None。"""
    tmp_dir = tempfile.mkdtemp()

    attempts = [
        (os.path.join(tmp_dir, "audio.m4a"),
         ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "copy"]),
        (os.path.join(tmp_dir, "audio.mp3"),
         ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k"]),
        (os.path.join(tmp_dir, "audio.wav"),
         ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000"]),
        (os.path.join(tmp_dir, "audio.mp3"),
         ["ffmpeg", "-y", "-i", video_path, "-vn"]),
    ]

    for out_path, cmd in attempts:
        try:
            subprocess.run(cmd + [out_path], check=True, capture_output=True)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except subprocess.CalledProcessError:
            continue

    return None


def _try_transcribe(file_path: str, language: str) -> str:
    """指定ファイルをWhisperに送って文字起こし。失敗時は空文字。"""
    try:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            result = _get_client().audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=(filename, f),
                language=language,
                response_format="verbose_json",
            )
        return _extract_text_from_result(result)
    except Exception:
        return ""


def _compress_audio(video_path: str) -> str | None:
    """大きい動画の場合、音声のみ抽出＋圧縮してWhisperサイズ制限内に。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                tmp,
            ],
            check=True, capture_output=True,
        )
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            return tmp
    except subprocess.CalledProcessError:
        pass
    return None


def transcribe(video_path: str, language: str = "ja") -> str:
    """動画を文字起こし。まず動画を直接Whisperに投げ、失敗時は音声抽出フォールバック。"""
    # Try 1: 動画ファイルを直接Whisperに送信（25MB未満）
    try:
        if os.path.getsize(video_path) < WHISPER_MAX_SIZE:
            text = _try_transcribe(video_path, language)
            if text:
                return text
    except OSError:
        pass

    # Try 2: 音声抽出してから送信
    audio_path = _extract_audio(video_path)
    if audio_path:
        try:
            text = _try_transcribe(audio_path, language)
            if text:
                return text
        finally:
            try:
                os.remove(audio_path)
            except OSError:
                pass

    # Try 3: 動画が25MB超過している場合、音声を圧縮して送信
    compressed = _compress_audio(video_path)
    if compressed:
        try:
            text = _try_transcribe(compressed, language)
            if text:
                return text
        finally:
            try:
                os.remove(compressed)
            except OSError:
                pass

    return ""
