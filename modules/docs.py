from modules.google_client import drive, docs


def create_doc_with_text(title: str, content: str, folder_id: str) -> str:
    """空のGoogle Docsをフォルダに作り、テキストを挿入してIDを返す。"""
    file = drive().files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [folder_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    doc_id = file["id"]

    if content:
        docs().documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": content,
                        }
                    }
                ]
            },
        ).execute()
    return doc_id
