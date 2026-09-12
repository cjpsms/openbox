#!/usr/bin/env python3
"""
Courier registration (openbox smart-locker demo, delivery side)

Minimal registration for the courier/delivery-side bot: a courier just
types their name, no ID card / vision extraction like the customer-side
telegram-bot. Stored separately in courier_users.json (its own registry,
not shared with telegram-bot's registered_users.json — a courier and a
customer are different kinds of accounts).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REGISTRY_FILE = Path(__file__).resolve().parent / "courier_users.json"


def _load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {}
    return json.loads(REGISTRY_FILE.read_text())


def _save_registry(registry: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


def is_registered(user_id: int) -> bool:
    return str(user_id) in _load_registry()


def get_registration(user_id: int) -> Optional[dict]:
    return _load_registry().get(str(user_id))


def register(user_id: int, name: str) -> None:
    registry = _load_registry()
    registry[str(user_id)] = {
        "name": name,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(registry)
