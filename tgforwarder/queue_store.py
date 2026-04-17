from __future__ import annotations

import json
from typing import Any

from .paths import QUEUE_PATH, ensure_data_dirs


class QueueStore:
    def __init__(self) -> None:
        ensure_data_dirs()

    def load(self) -> list[dict[str, Any]]:
        ensure_data_dirs()
        if not QUEUE_PATH.exists():
            return []
        try:
            data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            return []
        return []

    def save(self, items: list[dict[str, Any]]) -> None:
        ensure_data_dirs()
        QUEUE_PATH.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append(self, item: dict[str, Any]) -> None:
        items = self.load()
        items.append(item)
        self.save(items)

    def remove_first_match(self, state_key: str, message_id: int) -> None:
        items = self.load()
        new_items = []
        removed = False
        for item in items:
            if not removed and item.get("state_key") == state_key and int(item.get("message_id", -1)) == int(message_id):
                removed = True
                continue
            new_items.append(item)
        self.save(new_items)

