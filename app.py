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


STEP_LABELS = {
    "download": "ダウンロード",
    "transcribe": "文字起こし",
    "keyframes": "キーフレーム抽出",
    "analyze": "分析（Claude）",
    "folder": "フォルダ作成",
    "video_up": "動画UP",
    "thumbnail": "サムネイル生成",
    "transcript_doc": "文字起こしDoc",
    "analysis_doc": "分析Doc",
    "slides": "スライド生成",
}


def _delete_history_key(entry: dict, key: str):
    if key in entry:
        del entry[key]


def process_single_url(
    url: str,
    tmpdir: str,
    log,
    progress,
    prefix: str = "",
    prior_entry: dict | None = None,
) -> dict:
    """1本のURLを処理し、履歴エントリを返す。
    prior_entry が渡された場合、既存アーティファクト（フォルダ・Doc・Slides）を再利用して
    失敗ステップから再開する。成功時も失敗時も履歴エントリを返す（ステータス付き）。
    """
    # 前回の成果物を引き継ぐための入れ物
    state = dict(prior_entry) if prior_entry else {}
    # 過去の失敗情報はいったんクリア（今回リトライで上書きするため）
    for k in ("ステータス", "失敗ステップ", "エラー"):
        _delete_history_key(state, k)

    def _finalize_success() -> dict:
        state.update({"ステータス": "成功", "失敗ステップ": "", "エラー": ""})
        return state

    def _finalize_failure(step_key: str, err: Exception) -> dict:
        state.update({
            "ステータス": "失敗",
            "失敗ステップ": STEP_LABELS.get(step_key, step_key),
            "エラー": str(err),
            "実行日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "URL": url,
        })
        return state

    resumed = bool(prior_entry)
    if resumed:
        log.info(f"♻️ 既存エントリから再開します（失敗ステップ: {prior_entry.get('失敗ステップ', '不明')}）")

    # ---- 1. 動画DL ----
    filepath = None
    meta = None
    duration_sec = None
    try:
        progress.progress(10, text=f"{prefix}動画をダウンロード中…")
        filepath, meta = downloader.download_video(url, tmpdir)
        duration_sec = meta.get("duration") or downloader.get_duration_seconds(filepath)
        log.write(f"✅ ダウンロード完了：{os.path.basename(filepath)}（{fmt_duration(duration_sec)}）")
    except Exception as e:
        log.error(f"❌ ダウンロード失敗: {e}")
        return _finalize_failure("download", e)

    # ---- 2. 文字起こし（Whisper） ----
    transcript = state.get("_transcript")
    try:
        progress.progress(30, text=f"{prefix}文字起こし中（Whisper）…")
        if transcript:
            log.write(f"♻️ 文字起こし済み（{len(transcript)}文字）を再利用")
            with log.expander("文字起こし内容"):
                st.text(transcript)
        else:
            transcript_result = transcriber.transcribe(filepath)
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

            if transcribe_logs:
                with log.expander("🔍 文字起こし診断ログ"):
                    for line in transcribe_logs:
                        st.text(line)
            state["_transcript"] = transcript
    except Exception as e:
        log.error(f"❌ 文字起こし失敗: {e}")
        return _finalize_failure("transcribe", e)

    # ---- 3. キーフレーム抽出 ----
    key_frames = []
    try:
        progress.progress(45, text=f"{prefix}キーフレーム抽出中…")
        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        key_frames = downloader.extract_key_frames(filepath, frames_dir, count=8)
        log.write(f"✅ キーフレーム抽出完了（{len(key_frames)}枚）")
    except Exception as e:
        log.error(f"❌ キーフレーム抽出失敗: {e}")
        return _finalize_failure("keyframes", e)

    # ---- 4. 分析（Claude） ----
    analysis = state.get("_analysis")
    video_title = None
    try:
        progress.progress(55, text=f"{prefix}分析中（Claude Sonnet）…")
        if analysis:
            video_title = sanitize(analysis.get("video_title") or "無題動画", max_len=40)
            log.write(f"♻️ 分析済み（{video_title}）を再利用")
            with log.expander("分析結果（JSON）"):
                st.json(analysis)
        else:
            meta_for_ai = dict(meta)
            if not meta_for_ai.get("duration"):
                meta_for_ai["duration"] = duration_sec
            analysis = analyzer.analyze_video(transcript, key_frames, meta_for_ai)
            video_title = sanitize(analysis.get("video_title") or "無題動画", max_len=40)
            state["_analysis"] = analysis
            log.write(f"✅ 分析完了：**{video_title}**")
            with log.expander("分析結果（JSON）"):
                st.json(analysis)
        # 表示メタデータをこの時点で反映（以降のステップで失敗しても履歴に正しいタイトル等が残る）
        state.update({
            "タイトル": analysis.get("video_title", ""),
            "類型": analysis.get("classification", ""),
            "トーン": analysis.get("tone", ""),
            "プラットフォーム": downloader.platform_label(meta),
            "アカウント": f"{meta.get('uploader', '')} / @{meta.get('uploader_id', '')}",
            "尺": fmt_duration(duration_sec),
            "投稿日": fmt_upload_date(meta.get("upload_date")),
            "URL": url,
        })
    except Exception as e:
        log.error(f"❌ 分析失敗: {e}")
        return _finalize_failure("analyze", e)

    # ---- 5. フォルダ作成（既存があれば再利用） ----
    folder_id = state.get("_folder_id")
    try:
        progress.progress(65, text=f"{prefix}Driveフォルダを準備中…")
        if folder_id:
            log.markdown(f"♻️ 既存フォルダを再利用：[開く]({drive.folder_url(folder_id)})")
        else:
            folder_id = drive.create_folder(f"参考動画_{video_title}", config.SHARED_DRIVE_FOLDER_ID)
            drive.set_anyone_reader(folder_id)
            state["_folder_id"] = folder_id
            state["Driveフォルダ"] = drive.folder_url(folder_id)
            log.markdown(f"✅ フォルダ作成：[開く]({drive.folder_url(folder_id)})")
    except Exception as e:
        log.error(f"❌ フォルダ作成失敗: {e}")
        return _finalize_failure("folder", e)

    # ---- 6. 動画UP（既存があればスキップ） ----
    video_file_id = state.get("_video_file_id")
    try:
        progress.progress(70, text=f"{prefix}動画をDriveにUP中…")
        if video_file_id:
            log.write("♻️ 動画は既にUP済み（スキップ）")
        else:
            video_file_id = drive.upload_file(filepath, folder_id, name=f"{video_title}.mp4")
            drive.set_anyone_reader(video_file_id)
            state["_video_file_id"] = video_file_id
            log.write("✅ 動画UP完了")
    except Exception as e:
        log.error(f"❌ 動画UP失敗: {e}")
        return _finalize_failure("video_up", e)

    # ---- 7. サムネイル抽出＆UP（既存があればスキップ） ----
    thumb_url = state.get("_thumb_url")
    try:
        progress.progress(76, text=f"{prefix}サムネイルを生成・UP中…")
        if thumb_url:
            log.write("♻️ サムネイルは既にUP済み（スキップ）")
        else:
            thumb_local = os.path.join(tmpdir, f"{video_title}_thumb.png")
            try:
                downloader.extract_first_frame(filepath, thumb_local)
                thumb_id = drive.upload_file(thumb_local, folder_id, name=f"{video_title}_thumb.png", mimetype="image/png")
                drive.set_anyone_reader(thumb_id)
                thumb_url = f"https://drive.google.com/uc?export=view&id={thumb_id}"
                state["_thumb_url"] = thumb_url
                log.write("✅ サムネイル生成＆UP完了")
            except Exception as e:
                log.warning(f"⚠️ サムネイル生成失敗（スキップして継続）: {e}")
                thumb_url = None
    except Exception as e:
        log.error(f"❌ サムネイル処理失敗: {e}")
        return _finalize_failure("thumbnail", e)

    # ---- 8. 文字起こしDocs化（既存があればスキップ） ----
    transcript_doc_id = state.get("_transcript_doc_id")
    try:
        progress.progress(82, text=f"{prefix}文字起こしをDocsとしてUP中…")
        if transcript_doc_id:
            log.markdown(f"♻️ 文字起こしDocsは既に生成済み：[開く]({drive.file_url(transcript_doc_id, 'document')})")
        else:
            transcript_doc_id = docs.create_doc_with_text(
                f"文字起こし_{video_title}", transcript, folder_id
            )
            drive.set_anyone_reader(transcript_doc_id)
            state["_transcript_doc_id"] = transcript_doc_id
            state["文字起こしDoc"] = drive.file_url(transcript_doc_id, "document")
            log.markdown(f"✅ 文字起こしDocs：[開く]({drive.file_url(transcript_doc_id, 'document')})")
    except Exception as e:
        log.error(f"❌ 文字起こしDocs失敗: {e}")
        return _finalize_failure("transcript_doc", e)

    # ---- 9. 分析Docs化（既存があればスキップ） ----
    analysis_doc_id = state.get("_analysis_doc_id")
    try:
        progress.progress(90, text=f"{prefix}分析結果をDocsとしてUP中…")
        if analysis_doc_id:
            log.markdown(f"♻️ 分析Docsは既に生成済み：[開く]({drive.file_url(analysis_doc_id, 'document')})")
        else:
            analysis_text = format_analysis_doc(analysis, meta, url, duration_sec)
            analysis_doc_id = docs.create_doc_with_text(
                f"分析_{video_title}", analysis_text, folder_id
            )
            drive.set_anyone_reader(analysis_doc_id)
            state["_analysis_doc_id"] = analysis_doc_id
            state["分析Doc"] = drive.file_url(analysis_doc_id, "document")
            log.markdown(f"✅ 分析Docs：[開く]({drive.file_url(analysis_doc_id, 'document')})")
    except Exception as e:
        log.error(f"❌ 分析Docs失敗: {e}")
        return _finalize_failure("analysis_doc", e)

    # ---- 10. Slides化（既存があればスキップ） ----
    slides_id = state.get("_slides_id")
    try:
        progress.progress(94, text=f"{prefix}Slidesを生成中…")
        if slides_id:
            log.markdown(f"♻️ Slidesは既に生成済み：[開く]({drive.file_url(slides_id, 'presentation')})")
        else:
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

            try:
                slides.make_urls_clickable(slides_id, [url, transcript_doc_url, analysis_doc_url, folder_url])
                log.write("✅ URLをハイパーリンク化完了")
            except Exception as e:
                log.warning(f"⚠️ URLリンク化失敗（スキップ）: {e}")

            drive.set_anyone_reader(slides_id)
            state["_slides_id"] = slides_id
            state["Slides"] = drive.file_url(slides_id, "presentation")
            log.markdown(f"✅ Slides：[開く]({drive.file_url(slides_id, 'presentation')})")
    except Exception as e:
        log.error(f"❌ Slides生成失敗: {e}")
        return _finalize_failure("slides", e)

    progress.progress(100, text=f"{prefix}完了！")

    # 表示メタデータをまとめて上書き
    state.update({
        "実行日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "タイトル": analysis.get("video_title", ""),
        "類型": analysis.get("classification", ""),
        "トーン": analysis.get("tone", ""),
        "プラットフォーム": downloader.platform_label(meta),
        "アカウント": f"{meta.get('uploader', '')} / @{meta.get('uploader_id', '')}",
        "尺": fmt_duration(duration_sec),
        "投稿日": fmt_upload_date(meta.get("upload_date")),
        "URL": url,
    })
    return _finalize_success()


with tab_run:
    st.subheader("動画分析を実行")

    # 失敗した過去エントリがあれば、再実行ボタンを提示
    _failed = history.failed_entries()
    if _failed:
        with st.expander(f"⚠️ 前回までに **{len(_failed)}件** の失敗があります（同URLは既存フォルダを再利用して失敗ステップから再開できます）", expanded=False):
            for e in _failed[:10]:
                st.write(
                    f"- **{e.get('タイトル') or '(未分析)'}** "
                    f"｜失敗ステップ: `{e.get('失敗ステップ', '不明')}` "
                    f"｜{e.get('URL')}"
                )
            if len(_failed) > 10:
                st.caption(f"…他 {len(_failed) - 10} 件")
            if st.button(f"❌ 失敗した {len(_failed)} 件を再実行", type="primary", key="rerun_failed"):
                st.session_state["prefill_urls"] = "\n".join(e.get("URL", "") for e in _failed if e.get("URL"))
                st.rerun()

    default_urls = st.session_state.pop("prefill_urls", "")
    raw_urls = st.text_area(
        "投稿URL（複数指定可 — カンマ or 改行で区切る）",
        value=default_urls,
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
        results = {"success": [], "resumed": [], "fail": [], "skipped": []}

        overall_progress = st.progress(0, text=f"0 / {total} 完了")
        overall_status = st.empty()

        for idx, one_url in enumerate(urls, 1):
            prefix = f"[{idx}/{total}] "
            overall_status.info(f"🎬 {prefix}処理中：{one_url}")

            # 既存履歴を参照。成功済みならスキップ、失敗ならアーティファクトを引き継ぐ
            prior = history.find_by_url(one_url)
            if prior and prior.get("ステータス") == "成功":
                with st.expander(f"⏭ {prefix}{one_url}（既に成功済みのためスキップ）", expanded=False):
                    st.info(f"✅ 前回成功済み：{prior.get('タイトル', '')}")
                    st.markdown(f"📂 フォルダ：{prior.get('Driveフォルダ', '')}")
                results["skipped"].append(prior)
                overall_progress.progress(idx / total, text=f"{idx} / {total} 完了")
                continue

            with st.expander(f"🎬 {prefix}{one_url}", expanded=True):
                inner_log = st.container()
                inner_progress = st.progress(0, text=f"{prefix}開始します…")
                entry = None
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        entry = process_single_url(
                            one_url, tmpdir, inner_log, inner_progress,
                            prefix=prefix, prior_entry=prior,
                        )
                except Exception as e:
                    inner_log.error(f"❌ {prefix}想定外の失敗：{e}")
                    entry = {
                        "実行日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "URL": one_url,
                        "ステータス": "失敗",
                        "失敗ステップ": "想定外エラー",
                        "エラー": str(e),
                    }
                    if prior:
                        # 既存アーティファクトIDだけは温存
                        for k, v in prior.items():
                            if k.startswith("_") and k not in entry:
                                entry[k] = v

                if entry:
                    history.save_entry(entry)
                    if entry.get("ステータス") == "成功":
                        was_resumed = bool(prior)
                        (results["resumed"] if was_resumed else results["success"]).append(entry)
                        marker = "♻️ 再開して" if was_resumed else "✅ "
                        inner_log.success(f"{marker}{prefix}完了：{entry.get('タイトル', '')}")
                        inner_log.markdown(f"📂 フォルダ：{entry.get('Driveフォルダ', '')}")
                    else:
                        results["fail"].append(entry)
                        inner_log.error(
                            f"❌ {prefix}失敗（{entry.get('失敗ステップ', '不明')}）：{entry.get('エラー', '')}"
                        )

            overall_progress.progress(idx / total, text=f"{idx} / {total} 完了")

        overall_status.empty()
        st.divider()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("総数", total)
        col2.metric("成功", len(results["success"]) + len(results["resumed"]))
        col3.metric("失敗", len(results["fail"]))
        col4.metric("スキップ", len(results["skipped"]))

        if results["resumed"]:
            st.info(f"♻️ {len(results['resumed'])} 件は既存フォルダを再利用して再開しました。")

        if results["fail"]:
            with st.expander("❌ 失敗した動画", expanded=True):
                for f in results["fail"]:
                    st.write(f"- **URL**: {f.get('URL')}")
                    st.write(f"  - 失敗ステップ: `{f.get('失敗ステップ', '不明')}`")
                    st.write(f"  - エラー: `{f.get('エラー', '')}`")

        if results["success"] or results["resumed"]:
            st.success(
                f"新規 {len(results['success'])} 件 + 再開 {len(results['resumed'])} 件を分析完了。"
                "「実行履歴」タブで一覧確認できます。"
            )
            st.balloons()


with tab_history:
    st.subheader("実行履歴")
    hist = history.load()

    # ステータス欠損の後方互換：古いエントリはすべて成功として補完
    for e in hist:
        if "ステータス" not in e:
            e["ステータス"] = "成功"
            e["失敗ステップ"] = ""
            e["エラー"] = ""

    n_success = sum(1 for e in hist if e.get("ステータス") == "成功")
    n_fail = sum(1 for e in hist if e.get("ステータス") == "失敗")

    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 3])
    with col_a:
        if st.button("🔄 更新"):
            st.rerun()
    with col_b:
        st.metric("成功", n_success)
    with col_c:
        st.metric("失敗", n_fail)
    with col_d:
        filter_choice = st.radio(
            "表示フィルタ",
            options=["すべて", "成功のみ", "失敗のみ"],
            horizontal=True,
            label_visibility="collapsed",
        )

    if not hist:
        st.info("まだ実行履歴がありません。「動画分析」タブから実行してみてください。")
    else:
        if filter_choice == "成功のみ":
            visible = [e for e in hist if e.get("ステータス") == "成功"]
        elif filter_choice == "失敗のみ":
            visible = [e for e in hist if e.get("ステータス") == "失敗"]
        else:
            visible = hist

        if not visible:
            st.info(f"「{filter_choice}」に該当する履歴はありません。")
        else:
            df = pd.DataFrame(visible)
            # 内部フィールド（アンダースコア始まり）は非表示
            display_cols = [c for c in df.columns if not c.startswith("_")]
            df = df[display_cols]

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
                    "ステータス": st.column_config.TextColumn("状態", width="small"),
                    "失敗ステップ": st.column_config.TextColumn("失敗箇所", width="small"),
                    "エラー": st.column_config.TextColumn("エラー", width="medium"),
                },
            )

            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 CSVをダウンロード",
                data=csv,
                file_name=f"実行履歴_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )

        # 失敗行だけ抜粋表示（デバッグ用）
        failed = [e for e in hist if e.get("ステータス") == "失敗"]
        if failed:
            with st.expander(f"❌ 失敗した動画の詳細（{len(failed)}件）", expanded=False):
                for e in failed:
                    st.write(
                        f"- **URL**: {e.get('URL')}  \n"
                        f"  - 失敗箇所: `{e.get('失敗ステップ', '不明')}`  \n"
                        f"  - エラー: `{e.get('エラー', '')[:300]}`"
                    )
                if st.button(f"❌ 失敗した {len(failed)} 件を「動画分析」タブから再実行", key="rerun_from_history"):
                    st.session_state["prefill_urls"] = "\n".join(e.get("URL", "") for e in failed if e.get("URL"))
                    st.info("「🎬 動画分析」タブに移動し、URLが自動入力されるので **実行** ボタンを押してください。")

        with st.expander("⚠️ 履歴をクリア", expanded=False):
            st.warning("この操作は取り消せません。Drive上のファイルは削除されません。")
            if st.button("履歴をクリア", type="secondary"):
                history.clear()
                st.rerun()
