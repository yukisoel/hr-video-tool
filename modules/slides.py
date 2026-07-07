from modules.google_client import drive, slides


def copy_template(template_id: str, new_title: str, folder_id: str) -> str:
    """テンプレSlidesを共有ドライブの指定フォルダにコピーしてIDを返す。"""
    copy = drive().files().copy(
        fileId=template_id,
        body={"name": new_title, "parents": [folder_id]},
        supportsAllDrives=True,
    ).execute()
    return copy["id"]


def replace_placeholders(slides_id: str, replacements: dict[str, str]):
    """テンプレ内の {{key}} を values で一括置換。"""
    requests = []
    for key, value in replacements.items():
        requests.append({
            "replaceAllText": {
                "containsText": {"text": "{{" + key + "}}", "matchCase": True},
                "replaceText": (value or "").strip(),
            }
        })
    if requests:
        slides().presentations().batchUpdate(
            presentationId=slides_id,
            body={"requests": requests},
        ).execute()


def replace_shape_with_image(
    slides_id: str,
    placeholder_text: str,
    image_url: str,
    method: str = "CENTER_INSIDE",
):
    """指定テキストを含むshapeを画像に置き換え。
    CENTER_INSIDE: shape枠内に収まるように縮小・中央寄せ（アスペクト比保持、余白あり可）
    CENTER_CROP: shape枠を埋めるようにトリミング（アスペクト比保持、はみ出しはカット）
    """
    slides().presentations().batchUpdate(
        presentationId=slides_id,
        body={
            "requests": [{
                "replaceAllShapesWithImage": {
                    "containsText": {"text": placeholder_text, "matchCase": True},
                    "imageUrl": image_url,
                    "imageReplaceMethod": method,
                }
            }]
        },
    ).execute()


LINK_STYLE = {
    "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.11, "green": 0.29, "blue": 0.66}}},
    "underline": True,
}


def make_urls_clickable(slides_id: str, urls: list[str]):
    """スライド内で urls の各文字列が現れる箇所にハイパーリンクを付与。"""
    urls = [u for u in urls if u]
    if not urls:
        return
    pres = slides().presentations().get(presentationId=slides_id).execute()
    requests = []
    for slide in pres.get("slides", []):
        for element in slide.get("pageElements", []):
            shape = element.get("shape")
            if not shape:
                continue
            text = shape.get("text")
            if not text:
                continue
            for te in text.get("textElements", []):
                tr = te.get("textRun")
                if not tr:
                    continue
                content = tr.get("content", "")
                base_idx = te.get("startIndex", 0)
                for url in urls:
                    offset = 0
                    while True:
                        pos = content.find(url, offset)
                        if pos == -1:
                            break
                        requests.append({
                            "updateTextStyle": {
                                "objectId": element["objectId"],
                                "textRange": {
                                    "type": "FIXED_RANGE",
                                    "startIndex": base_idx + pos,
                                    "endIndex": base_idx + pos + len(url),
                                },
                                "style": {
                                    "link": {"url": url},
                                    **LINK_STYLE,
                                },
                                "fields": "link,foregroundColor,underline",
                            }
                        })
                        offset = pos + len(url)
    if requests:
        slides().presentations().batchUpdate(
            presentationId=slides_id, body={"requests": requests}
        ).execute()
