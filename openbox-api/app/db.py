"""
SQLite storage for openbox-api.

Plain sqlite3 (WAL mode) behind a single module-level connection guarded
by a lock — this app expects light concurrency (2 Telegram bots + one
in-process camera loop), so a full async DB driver would be overkill.
Every write goes through `db()` inside a `with LOCK:` block; see the
router files for the pattern.
"""

import sqlite3
import threading
from datetime import datetime, timezone

from . import config

LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    student_code TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS riders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    company TEXT,
    employee_id TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
        -- NEW -> PENDING_APPROVAL -> APPROVED | REJECTED
    card_reason TEXT,
    card_photo_path TEXT,
    registered_at TEXT NOT NULL,
    card_checked_at TEXT,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT UNIQUE NOT NULL,
    size TEXT NOT NULL,            -- 'small' | 'large'
    status TEXT NOT NULL DEFAULT 'FREE'
        -- FREE -> OPEN_FOR_DROP -> STORED -> OPEN_FOR_PICKUP -> FREE
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    box_size TEXT NOT NULL,
    box_id INTEGER REFERENCES boxes(id),
    status TEXT NOT NULL DEFAULT 'AWAITING_PAYMENT',
        -- AWAITING_PAYMENT -> WAITING_DROP -> STORED -> OPEN_FOR_PICKUP
        -- -> DONE | CANCELLED | EXPIRED
    order_platform TEXT,
    order_id TEXT,
    recipient_name TEXT,
    phone_last4 TEXT,
    tracking_no TEXT,
    amount_due_thb REAL NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    stored_at TEXT,
    picked_up_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id),
    amount_thb REAL NOT NULL,
    trans_ref TEXT UNIQUE,
    verified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drop_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rider_id INTEGER NOT NULL REFERENCES riders(id),
    status TEXT NOT NULL DEFAULT 'OPEN',
        -- OPEN -> MATCHED | MULTI_MATCH | NO_MATCH -> COMPLETED
        --      -> EXPIRED | CANCELLED | ERROR
    reservation_id INTEGER REFERENCES reservations(id),
    candidates_json TEXT,
    note TEXT,
    label_photo_path TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,  -- rider_card | order_screenshot | slip | label_photo | clip
    path TEXT NOT NULL,
    related_table TEXT,
    related_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail_json TEXT
);
"""

_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db() -> None:
    with LOCK:
        db().executescript(SCHEMA)
        db().commit()
        _seed_boxes()


def _seed_boxes() -> None:
    """Seed the 2 physical boxes from the finalized hardware plan, if empty."""
    cur = db().execute("SELECT COUNT(*) FROM boxes")
    if cur.fetchone()[0] > 0:
        return
    db().executemany(
        "INSERT INTO boxes (label, size, status) VALUES (?, ?, 'FREE')",
        [("box-1", "large"), ("box-2", "small")],
    )
    db().commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(kind: str, detail_json: str = "{}") -> None:
    with LOCK:
        db().execute(
            "INSERT INTO events (ts, kind, detail_json) VALUES (?, ?, ?)",
            (now_iso(), kind, detail_json),
        )
        db().commit()


def insert_media(kind: str, path: str, related_table: str | None, related_id: int | None) -> None:
    with LOCK:
        db().execute(
            "INSERT INTO media (kind, path, related_table, related_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, path, related_table, related_id, now_iso()),
        )
        db().commit()
