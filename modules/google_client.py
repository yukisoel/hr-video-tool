from google.oauth2 import service_account
from googleapiclient.discovery import build
import config

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/presentations",
]

_creds = None


def _get_creds():
    global _creds
    if _creds is not None:
        return _creds

    # Streamlit Secrets（デプロイ環境）を優先
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "google_service_account" in st.secrets:
            info = dict(st.secrets["google_service_account"])
            _creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            return _creds
    except Exception:
        pass

    # フォールバック：ローカルJSON（ローカル開発環境）
    _creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    return _creds


def drive():
    return build("drive", "v3", credentials=_get_creds(), cache_discovery=False)


def docs():
    return build("docs", "v1", credentials=_get_creds(), cache_discovery=False)


def slides():
    return build("slides", "v1", credentials=_get_creds(), cache_discovery=False)
