"""Small fuzzy-matching helpers (stdlib difflib, no extra dependency)."""

import difflib
import re


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9ก-๙\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def name_similarity(a: str | None, b: str | None) -> float:
    """0.0-1.0 similarity ratio between two names, order-insensitive-ish."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def names_match(a: str | None, b: str | None, threshold: float = 0.6) -> bool:
    return name_similarity(a, b) >= threshold
