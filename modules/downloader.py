import os
import subprocess
import yt_dlp


def get_duration_seconds(filepath: str) -> int | None:
    """ffprobeで動画尺(秒)を返す。yt-dlpのメタが欠けている場合のフォールバック。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, check=True,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return None


def extract_first_frame(video_path: str, output_path: str) -> str:
    """動画の0秒時点のフレームをPNGとして保存。アスペクト比はそのまま。"""
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-ss", "0", "-frames:v", "1", output_path],
        capture_output=True, check=True,
    )
    return output_path


def extract_key_frames(video_path: str, output_dir: str, count: int = 8) -> list[str]:
    """動画から等間隔で count 枚のキーフレームを抽出。
    幅720pxにリサイズしてトークン節約。Claude画像分析用。"""
    duration = get_duration_seconds(video_path) or 30
    # 動画を均等分割してその中点でサンプリング
    step = duration / count
    frames = []
    for i in range(count):
        t = step * (i + 0.5)
        out = os.path.join(output_dir, f"frame_{i:02d}.jpg")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{t:.2f}",
                    "-i", video_path,
                    "-frames:v", "1",
                    "-vf", "scale='min(720,iw)':-2",
                    "-q:v", "3",
                    out,
                ],
                capture_output=True, check=True,
            )
            if os.path.exists(out) and os.path.getsize(out) > 0:
                frames.append(out)
        except subprocess.CalledProcessError:
            continue
    return frames


def _has_video_and_audio_streams(video_path: str) -> tuple[bool, bool]:
    """(has_video, has_audio) を返す。"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        stdout = result.stdout
        return ("video" in stdout, "audio" in stdout)
    except Exception:
        return (False, False)


def _download_with_format(url: str, output_dir: str, fmt: str) -> tuple[str, dict]:
    """指定フォーマットでダウンロード試行。"""
    ydl_opts = {
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        # 拡張子補正
        if not os.path.exists(filepath):
            base = os.path.splitext(filepath)[0]
            for ext in (".mp4", ".mkv", ".webm", ".m4a"):
                candidate = base + ext
                if os.path.exists(candidate):
                    filepath = candidate
                    break
    return filepath, info


def download_video(url: str, output_dir: str) -> tuple[str, dict]:
    """IG/TikTok/YouTube等のURLから動画をダウンロード。破損ファイル検知→再試行。
    Returns: (ローカルファイルパス, メタ情報dict)"""
    os.makedirs(output_dir, exist_ok=True)

    # フォーマット候補：シンプル→複雑の順で試す。1つ目が最も安定
    format_candidates = [
        "best",                        # 単一の最良ストリーム（TikTokで最も安定）
        "best[ext=mp4]/best",          # mp4優先
        "bestvideo*+bestaudio/best",   # マージ型（最後の手段）
    ]

    filepath = None
    info = None
    for fmt in format_candidates:
        try:
            filepath, info = _download_with_format(url, output_dir, fmt)
            if not filepath or not os.path.exists(filepath):
                continue
            has_video, has_audio = _has_video_and_audio_streams(filepath)
            # 動画ストリームがあればOK。音声はなくてもよい（音声のない動画もあり得る）
            if has_video:
                break
            # 破損ファイル → 削除して次のフォーマットへ
            try:
                os.remove(filepath)
            except OSError:
                pass
            filepath = None
        except Exception:
            continue

    if not filepath or not os.path.exists(filepath):
        raise RuntimeError(f"ダウンロード失敗（全フォーマット試行済み）: {url}")

    meta = {
        "id": info.get("id", ""),
        "title": info.get("title") or info.get("description") or info.get("id") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "uploader_id": info.get("uploader_id") or "",
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),  # YYYYMMDD
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
    }
    return filepath, meta


def platform_label(meta: dict) -> str:
    """extractor から人間可読なプラットフォーム名を推定。"""
    ext = (meta.get("extractor") or "").lower()
    if "instagram" in ext:
        return "Instagram Reels"
    if "tiktok" in ext:
        return "TikTok"
    if "youtube" in ext:
        return "YouTube Shorts / 通常"
    return meta.get("extractor") or "不明"
