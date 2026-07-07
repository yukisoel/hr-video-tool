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


def save_entry(entry: dict[str, Any]):
    history = load()
    history.insert(0, entry)  # 新しい順
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def clear():
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
