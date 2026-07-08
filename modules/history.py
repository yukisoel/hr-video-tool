import json
import os
from typing import Any

HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "history.json",
)


def load() -> list[dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_all(entries: list[dict[str, Any]]):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def save_entry(entry: dict[str, Any]):
    """新しい実行結果を先頭に追加。ただしURLが既存の場合は上書きする（再実行対応）。"""
    entries = load()
    url = entry.get("URL")
    if url:
        # 既存の同URLエントリを削除
        entries = [e for e in entries if e.get("URL") != url]
    entries.insert(0, entry)
    _write_all(entries)


def find_by_url(url: str) -> dict[str, Any] | None:
    """URLに一致する既存エントリを返す。"""
    if not url:
        return None
    for e in load():
        if e.get("URL") == url:
            return e
    return None


def failed_entries() -> list[dict[str, Any]]:
    return [e for e in load() if e.get("ステータス") == "失敗"]


def clear():
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
