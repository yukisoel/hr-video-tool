import os
import re
import tempfile
from datetime import datetime

import streamlit as st
import pandas as pd

import config
from modules import downloader, transcriber, analyzer, drive, docs, slides, history


st.set_page_config(page_title="HR動画分析ツール", page_icon="🎬", layout="wide")
st.title("🎬 HR動画分析ツール")
st.caption("IG / TikTok URLを入れると、Google Driveにフォルダ・動画・文字起こし・分析Docs・スライドを一括生成します。")

# --- 設定チェック ---
errors = config.validate()
if errors:
    st.error("環境設定に不足があります：")
    for e in errors:
        st.write(f"- {e}")
    st.stop()


def sanitize(name: str, max_len: int = 40) -> str:
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", name).strip()
    return name[:max_len] or "untitled"


def fmt_duration(seconds) -> str:
    if not seconds:
        return "尺不明"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "尺不明"
    if seconds < 60:
        return f"約{seconds}秒"
    m, s = divmod(seconds, 60)
    return f"約{m}分{s}秒" if s else f"約{m}分"


def fmt_upload_date(yyyymmdd: str | None) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return ""
    return f"{yyyymmdd[0:4]}年{yyyymmdd[4:6]}月{yyyymmdd[6:8]}日"


def format_analysis_doc(analysis: dict, meta: dict, url: str, duration_sec) -> str:
    lines = [
        "# 参考動画分析",
        "",
        f"タイトル: {analysis.get('video_title', '')}",
        f"サブタイトル: {analysis.get('summary_title', '')}",
        f"類型: {analysis.get('classification', '')}",
        f"トーン: {analysis.get('tone', '')}",
        f"URL: {url}",
        f"プラットフォーム: {downloader.platform_label(meta)}",
        f"アカウント名: {meta.get('uploader', '')} / @{meta.get('uploader_id', '')}",
        f"尺: {fmt_duration(duration_sec)}",
        f"投稿日: {fmt_upload_date(meta.get('upload_date'))}",
        "",
        "## フック（冒頭2秒で何が起きるか）",
    ]
    for i, s in enumerate(analysis.get("hook", []), 1):
        lines.append(f"{i}. {s}")
    lines += ["", "## 構成メモ（本編の展開・編集の特徴）"]
    for i, s in enumerate(analysis.get("structure", []), 1):
        lines.append(f"{i}. {s}")
    lines += ["", "## 転用ポイント（貴社版で真似る要素）"]
    for i, s in enumerate(analysis.get("adaptation", []), 1):
        lines.append(f"{i}. {s}")
    return "\n".join(lines)


def build_slide_replacements(
    analysis: dict,
    meta: dict,
    url: str,
    duration_sec,
    transcript_doc_url: str = "",
    analysis_doc_url: str = "",
    folder_url: str = "",
) -> dict:
    hook = analysis.get("hook", []) + ["", ""]
    structure = analysis.get("structure", []) + ["", "", ""]
    adaptation = analysis.get("adaptation", []) + ["", "", ""]
    return {
        "動画タイトル": analysis.get("video_title", ""),
        "タイトル": analysis.get("summary_title", ""),
        "類型": analysis.get("classification", ""),
        "トーン": analysis.get("tone", ""),
        "URL": url,
        "動画URL": url,
        "プラットフォーム": downloader.platform_label(meta),
        "アカウント名": f"{meta.get('uploader', '')} / @{meta.get('uploader_id', '')}",
        "尺": fmt_duration(duration_sec),
        "投稿日": fmt_upload_date(meta.get("upload_date")),
        "フック1": hook[0],
        "フック2": hook[1],
        "構成メモ1": structure[0],
        "構成メモ2": structure[1],
        "構成メモ3": structure[2],
        "転用ポイント1": adaptation[0],
        "転用ポイント2": adaptation[1],
        "転用ポイント3": adaptation[2],
        "文字起こしURL": transcript_doc_url,
        "分析URL": analysis_doc_url,
        "フォルダURL": folder_url,
    }


# --- タブ ---
tab_run, tab_history = st.tabs(["🎬 動画分析", "📊 実行履歴"])


def parse_urls(raw: str) -> list[str]:
    """カンマまたは改行で区切られた入力から、URLだけを抽出。"""
    if not raw:
        return []
    parts = re.split(r"[,\n\r]+", raw)
    return [p.strip() for p in parts if p.strip()]


def process_single_url(url: str, tmpdir: str, log, progress, prefix: str = "") -> dict | None:
    """1本のURLを最後まで処理し、履歴エントリを返す。失敗時はNone。"""
    # 1. 動画DL
    progress.progress(10, text=f"{prefix}動画をダウンロード中…")
    filepath, meta = downloader.download_video(url, tmpdir)
    duration_sec = meta.get("duration") or downloader.get_duration_seconds(filepath)
    log.write(f"✅ ダウンロード完了：{os.path.basename(filepath)}（{fmt_duration(duration_sec)}）")

    # 2. 文字起こし（Whisper）
    progress.progress(30, text=f"{prefix}文字起こし中（Whisper）…")
    transcript_result = transcriber.transcribe(filepath)
    # 後方互換：戻り値がタプル(text, logs)かstr（旧仕様）か判定
    if isinstance(transcript_result, tuple):
        transcript, transcribe_logs = transcript_result
    else:
        transcript = transcript_result
        transcribe_logs = []

    if transcript:
        log.write(f"✅ 文字起こし完了（{len(transcript)}文字）")
        with log.expander("文字起こし内容"):
            st.text(transcript)
    else:
        log.warning("⚠️ 音声抽出失敗 → 視覚のみで分析継続")
        transcript = "（音声なし／文字起こし不可）"

    # 診断ログを常に表示（成功時も失敗時も）
    if transcribe_logs:
        with log.expander("🔍 文字起こし診断ログ"):
            for line in transcribe_logs:
                st.text(line)

    # 3. キーフレーム抽出（Claude画像分析用）
    progress.progress(45, text=f"{prefix}キーフレーム抽出中…")
    frames_dir = os.path.join(tmpdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    key_frames = downloader.extract_key_frames(filepath, frames_dir, count=8)
    log.write(f"✅ キーフレーム抽出完了（{len(key_frames)}枚）")

    # 4. 分析（Claude：文字起こし+画像）
    progress.progress(55, text=f"{prefix}分析中（Claude Sonnet）…")
    meta_for_ai = dict(meta)
    if not meta_for_ai.get("duration"):
        meta_for_ai["duration"] = duration_sec
    analysis = analyzer.analyze_video(transcript, key_frames, meta_for_ai)
    video_title = sanitize(analysis.get("video_title") or "無題動画", max_len=40)
    log.write(f"✅ 分析完了：**{video_title}**")
    with log.expander("分析結果（JSON）"):
        st.json(analysis)

    # 4. フォルダ作成
    progress.progress(65, text=f"{prefix}Driveフォルダを作成中…")
    folder_id = drive.create_folder(f"参考動画_{video_title}", config.SHARED_DRIVE_FOLDER_ID)
    drive.set_anyone_reader(folder_id)
    log.markdown(f"✅ フォルダ作成：[開く]({drive.folder_url(folder_id)})")

    # 5. 動画UP
    progress.progress(70, text=f"{prefix}動画をDriveにUP中…")
    video_id = drive.upload_file(filepath, folder_id, name=f"{video_title}.mp4")
    drive.set_anyone_reader(video_id)
    log.write("✅ 動画UP完了")

    # 5-b. サムネイル抽出＆UP
    progress.progress(76, text=f"{prefix}サムネイルを生成・UP中…")
    thumb_local = os.path.join(tmpdir, f"{video_title}_thumb.png")
    thumb_url = None
    try:
        downloader.extract_first_frame(filepath, thumb_local)
        thumb_id = drive.upload_file(thumb_local, folder_id, name=f"{video_title}_thumb.png", mimetype="image/png")
        drive.set_anyone_reader(thumb_id)
        thumb_url = f"https://drive.google.com/uc?export=view&id={thumb_id}"
        log.write("✅ サムネイル生成＆UP完了")
    except Exception as e:
        log.warning(f"⚠️ サムネイル生成失敗（スキップして継続）: {e}")

    # 6. 文字起こしDocs化
    progress.progress(82, text=f"{prefix}文字起こしをDocsとしてUP中…")
    transcript_doc_id = docs.create_doc_with_text(
        f"文字起こし_{video_title}", transcript, folder_id
    )
    drive.set_anyone_reader(transcript_doc_id)
    log.markdown(f"✅ 文字起こしDocs：[開く]({drive.file_url(transcript_doc_id, 'document')})")

    # 7. 分析Docs化
    progress.progress(90, text=f"{prefix}分析結果をDocsとしてUP中…")
    analysis_text = format_analysis_doc(analysis, meta, url, duration_sec)
    analysis_doc_id = docs.create_doc_with_text(
        f"分析_{video_title}", analysis_text, folder_id
    )
    drive.set_anyone_reader(analysis_doc_id)
    log.markdown(f"✅ 分析Docs：[開く]({drive.file_url(analysis_doc_id, 'document')})")

    # 8. Slides化
    progress.progress(94, text=f"{prefix}Slidesを生成中…")
    slides_id = slides.copy_template(
        config.TEMPLATE_SLIDES_ID,
        f"参考動画スライド_{video_title}",
        folder_id,
    )
    transcript_doc_url = drive.file_url(transcript_doc_id, "document")
    analysis_doc_url = drive.file_url(analysis_doc_id, "document")
    folder_url = drive.folder_url(folder_id)
    replacements = build_slide_replacements(
        analysis, meta, url, duration_sec,
        transcript_doc_url=transcript_doc_url,
        analysis_doc_url=analysis_doc_url,
        folder_url=folder_url,
    )
    slides.replace_placeholders(slides_id, replacements)

    # 8-b. サムネイル画像を差し込み（複数の候補テキストにマッチ）
    if thumb_url:
        placeholder_candidates = [
            "{{サムネイル}}",
            "サムネイル貼り付け枠",
            "サムネイル 貼り付け枠",
        ]
        for placeholder in placeholder_candidates:
            try:
                slides.replace_shape_with_image(slides_id, placeholder, thumb_url, method="CENTER_INSIDE")
            except Exception:
                pass
        log.write("✅ サムネイル画像をスライドに差し込み完了（マッチ枠のみ）")

    # 8-c. URLを可クリック化
    try:
        slides.make_urls_clickable(slides_id, [url, transcript_doc_url, analysis_doc_url, folder_url])
        log.write("✅ URLをハイパーリンク化完了")
    except Exception as e:
        log.warning(f"⚠️ URLリンク化失敗（スキップ）: {e}")

    drive.set_anyone_reader(slides_id)
    log.markdown(f"✅ Slides：[開く]({drive.file_url(slides_id, 'presentation')})")

    progress.progress(100, text=f"{prefix}完了！")

    entry = {
        "実行日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "タイトル": analysis.get("video_title", ""),
        "類型": analysis.get("classification", ""),
        "トーン": analysis.get("tone", ""),
        "プラットフォーム": downloader.platform_label(meta),
        "アカウント": f"{meta.get('uploader', '')} / @{meta.get('uploader_id', '')}",
        "尺": fmt_duration(duration_sec),
        "投稿日": fmt_upload_date(meta.get("upload_date")),
        "URL": url,
        "Driveフォルダ": drive.folder_url(folder_id),
        "文字起こしDoc": drive.file_url(transcript_doc_id, "document"),
        "分析Doc": drive.file_url(analysis_doc_id, "document"),
        "Slides": drive.file_url(slides_id, "presentation"),
    }
    return entry


with tab_run:
    st.subheader("動画分析を実行")
    raw_urls = st.text_area(
        "投稿URL（複数指定可 — カンマ or 改行で区切る）",
        placeholder=(
            "https://www.tiktok.com/@example/video/xxxxxxx\n"
            "https://www.instagram.com/reel/yyyyyy/\n"
            "https://www.tiktok.com/@another/video/zzzzzzz"
        ),
        height=140,
    )

    urls = parse_urls(raw_urls)
    if urls:
        st.caption(f"📋 検出されたURL：**{len(urls)}件**")
    run = st.button("実行", type="primary", disabled=not urls)

    if run:
        total = len(urls)
        results = {"success": [], "fail": []}

        # 全体進捗
        overall_progress = st.progress(0, text=f"0 / {total} 完了")
        overall_status = st.empty()

        for idx, one_url in enumerate(urls, 1):
            prefix = f"[{idx}/{total}] "
            overall_status.info(f"🎬 {prefix}処理中：{one_url}")
            with st.expander(f"🎬 {prefix}{one_url}", expanded=True):
                inner_log = st.container()
                inner_progress = st.progress(0, text=f"{prefix}開始します…")
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        entry = process_single_url(one_url, tmpdir, inner_log, inner_progress, prefix=prefix)
                        if entry:
                            history.save_entry(entry)
                            results["success"].append(entry)
                            inner_log.success(f"✅ {prefix}完了：{entry.get('タイトル', '')}")
                            inner_log.markdown(f"📂 フォルダ：{entry['Driveフォルダ']}")
                except Exception as e:
                    results["fail"].append({"url": one_url, "error": str(e)})
                    inner_log.error(f"❌ {prefix}失敗：{e}")

            overall_progress.progress(idx / total, text=f"{idx} / {total} 完了")

        # 全体サマリ
        overall_status.empty()
        st.divider()
        col1, col2, col3 = st.columns(3)
        col1.metric("総数", total)
        col2.metric("成功", len(results["success"]))
        col3.metric("失敗", len(results["fail"]))

        if results["fail"]:
            with st.expander("❌ 失敗した動画", expanded=True):
                for f in results["fail"]:
                    st.write(f"- **URL**: {f['url']}")
                    st.write(f"  - エラー: `{f['error']}`")

        if results["success"]:
            st.success(f"{len(results['success'])}件の動画を分析しました。「実行履歴」タブで一覧確認できます。")
            st.balloons()


with tab_history:
    st.subheader("実行履歴")
    hist = history.load()
    col_a, col_b = st.columns([1, 5])
    with col_a:
        if st.button("🔄 更新"):
            st.rerun()
    with col_b:
        st.caption(f"件数: {len(hist)}")

    if not hist:
        st.info("まだ実行履歴がありません。「動画分析」タブから実行してみてください。")
    else:
        df = pd.DataFrame(hist)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("URL", display_text="🔗 元動画"),
                "Driveフォルダ": st.column_config.LinkColumn("Driveフォルダ", display_text="📂 開く"),
                "文字起こしDoc": st.column_config.LinkColumn("文字起こし", display_text="📝 開く"),
                "分析Doc": st.column_config.LinkColumn("分析", display_text="📊 開く"),
                "Slides": st.column_config.LinkColumn("Slides", display_text="🎞 開く"),
                "実行日時": st.column_config.TextColumn("実行日時", width="small"),
                "タイトル": st.column_config.TextColumn("タイトル", width="medium"),
                "プラットフォーム": st.column_config.TextColumn("プラットフォーム", width="small"),
                "アカウント": st.column_config.TextColumn("アカウント", width="medium"),
                "尺": st.column_config.TextColumn("尺", width="small"),
                "投稿日": st.column_config.TextColumn("投稿日", width="small"),
            },
        )

        # CSVダウンロード
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 CSVをダウンロード",
            data=csv,
            file_name=f"実行履歴_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

        with st.expander("⚠️ 履歴をクリア", expanded=False):
            st.warning("この操作は取り消せません。Drive上のファイルは削除されません。")
            if st.button("履歴をクリア", type="secondary"):
                history.clear()
                st.rerun()
