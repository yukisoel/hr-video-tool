import os
from googleapiclient.http import MediaFileUpload
from modules.google_client import drive


def create_folder(name: str, parent_id: str) -> str:
    """共有ドライブ配下にフォルダを作成しIDを返す。"""
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = drive().files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


def upload_file(local_path: str, folder_id: str, name: str | None = None,
                mimetype: str = "video/mp4") -> str:
    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    metadata = {
        "name": name or os.path.basename(local_path),
        "parents": [folder_id],
    }
    file = drive().files().create(
        body=metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return file["id"]


def set_anyone_reader(file_id: str):
    """全ユーザーに閲覧権限を付与。共有ドライブ配下でも動く。"""
    drive().permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True,
    ).execute()


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def file_url(file_id: str, mime_hint: str = "file") -> str:
    if mime_hint == "document":
        return f"https://docs.google.com/document/d/{file_id}/edit"
    if mime_hint == "presentation":
        return f"https://docs.google.com/presentation/d/{file_id}/edit"
    return f"https://drive.google.com/file/d/{file_id}/view"
