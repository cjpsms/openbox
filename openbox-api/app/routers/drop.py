"""
Rider drop-off flow: arm the scanner, capture the parcel label, match it
against a WAITING_DROP reservation, assign a box, open it.

cam2 (scanner) is disabled on cj's dev laptop by default (only one
physical webcam — see camera.py) so _run_drop_session below will end in
status ERROR until CAM2_INDEX is set; everything else in this router
(matching, box assignment, serial_link) works standalone and can be
exercised via POST /drop-sessions/{id}/pick + /confirm using a manually
inserted label_photo/candidates for now.
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, Form, HTTPException

from .. import camera, config, db, matching, serial_link, telegram_push, vision

router = APIRouter(prefix="/drop-sessions", tags=["drop"])


def _row_or_404(session_id: int):
    row = db.db().execute("SELECT * FROM drop_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "drop session not found")
    return row


@router.post("")
async def start_drop_session(tg_user_id: int = Form(...)):
    rider = db.db().execute("SELECT * FROM riders WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
    if rider is None:
        raise HTTPException(403, "rider not registered")
    if rider["status"] != "APPROVED":
        raise HTTPException(403, f"rider status is {rider['status']}, not APPROVED")

    now = db.now_iso()
    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=config.DROP_SESSION_TTL_SECONDS)).isoformat()

    with db.LOCK:
        cur = db.db().execute(
            "INSERT INTO drop_sessions (rider_id, status, created_at, expires_at) "
            "VALUES (?, 'OPEN', ?, ?)",
            (rider["id"], now, expires_at),
        )
        db.db().commit()
        session_id = cur.lastrowid

    db.log_event("drop_session_opened", f'{{"id": {session_id}, "rider_tg_id": {tg_user_id}}}')
    asyncio.create_task(_run_drop_session(session_id))
    return dict(_row_or_404(session_id))


async def _run_drop_session(session_id: int) -> None:
    save_path = config.MEDIA_DIR / "label_photos" / f"{uuid.uuid4().hex}.jpg"
    try:
        photo_path = await camera.capture_label_photo(save_path, timeout_seconds=config.DROP_SESSION_TTL_SECONDS)
    except camera.ScannerTimeout:
        _set_session(session_id, "EXPIRED", note="ไม่พบพัสดุมาแปะสแกนเนอร์ภายในเวลาที่กำหนด")
        return
    except RuntimeError as e:
        _set_session(session_id, "ERROR", note=str(e))
        return

    with db.LOCK:
        db.db().execute(
            "UPDATE drop_sessions SET label_photo_path=? WHERE id=?", (str(photo_path), session_id)
        )
        db.db().commit()

    try:
        label = await vision.extract_json(str(photo_path), vision.PARCEL_LABEL_PROMPT)
    except RuntimeError as e:
        _set_session(session_id, "ERROR", note=f"vision extraction failed: {e}")
        return

    candidates = _match_reservations(label)
    if not candidates:
        _set_session(session_id, "NO_MATCH", note=json.dumps(label, ensure_ascii=False))
    elif len(candidates) == 1:
        _set_session(session_id, "MATCHED", reservation_id=candidates[0]["id"])
    else:
        _set_session(
            session_id, "MULTI_MATCH",
            note=json.dumps([c["id"] for c in candidates]),
        )

    db.log_event(
        "drop_session_matched",
        json.dumps({"id": session_id, "candidates": [c["id"] for c in candidates]}),
    )


def _match_reservations(label: dict) -> list:
    rows = db.db().execute("SELECT * FROM reservations WHERE status='WAITING_DROP'").fetchall()
    tracking_no = (label.get("tracking_no") or "").strip()
    recipient_name = label.get("recipient_name")
    phone_last4 = (label.get("phone_last4") or "").strip()

    if tracking_no:
        exact = [r for r in rows if (r["tracking_no"] or "").strip() == tracking_no]
        if exact:
            return exact

    candidates = []
    for r in rows:
        name_ok = matching.names_match(recipient_name, r["recipient_name"], threshold=0.6)
        phone_ok = bool(phone_last4) and phone_last4 == (r["phone_last4"] or "").strip()
        if name_ok and (phone_ok or not r["phone_last4"]):
            candidates.append(r)
    return candidates


def _set_session(session_id: int, status: str, reservation_id: int | None = None, note: str | None = None) -> None:
    with db.LOCK:
        db.db().execute(
            "UPDATE drop_sessions SET status=?, reservation_id=?, note=? WHERE id=?",
            (status, reservation_id, note, session_id),
        )
        db.db().commit()


@router.get("/{session_id}")
async def get_drop_session(session_id: int):
    return dict(_row_or_404(session_id))


@router.post("/{session_id}/pick")
async def pick_candidate(session_id: int, reservation_id: int = Form(...)):
    session = _row_or_404(session_id)
    if session["status"] != "MULTI_MATCH":
        raise HTTPException(409, f"session status is {session['status']}, not MULTI_MATCH")
    candidate_ids = json.loads(session["note"] or "[]")
    if reservation_id not in candidate_ids:
        raise HTTPException(400, "reservation_id is not among this session's candidates")
    _set_session(session_id, "MATCHED", reservation_id=reservation_id)
    return dict(_row_or_404(session_id))


@router.post("/{session_id}/confirm")
async def confirm_drop(session_id: int):
    session = _row_or_404(session_id)
    if session["status"] != "MATCHED":
        raise HTTPException(409, f"session status is {session['status']}, not MATCHED")

    reservation = db.db().execute(
        "SELECT * FROM reservations WHERE id = ?", (session["reservation_id"],)
    ).fetchone()
    if reservation is None or reservation["status"] != "WAITING_DROP":
        raise HTTPException(409, "matched reservation is no longer WAITING_DROP")

    box = db.db().execute(
        "SELECT * FROM boxes WHERE status='FREE' AND size=? LIMIT 1", (reservation["box_size"],)
    ).fetchone()
    if box is None:
        raise HTTPException(409, f"ไม่มีกล่องขนาด {reservation['box_size']} ว่างตอนนี้")

    with db.LOCK:
        db.db().execute("UPDATE boxes SET status='OPEN_FOR_DROP' WHERE id=?", (box["id"],))
        db.db().execute(
            "UPDATE reservations SET box_id=? WHERE id=?", (box["id"], reservation["id"])
        )
        db.db().execute("UPDATE drop_sessions SET status='COMPLETED' WHERE id=?", (session_id,))
        db.db().commit()

    await serial_link.open_box(box["label"])
    db.log_event(
        "drop_confirmed",
        json.dumps({"session_id": session_id, "reservation_id": reservation["id"], "box": box["label"]}),
    )

    customer_tg_id = db.db().execute(
        "SELECT tg_user_id FROM customers WHERE id = ?", (reservation["customer_id"],)
    ).fetchone()["tg_user_id"]
    asyncio.create_task(_finish_drop(reservation["id"], box["id"], box["label"], customer_tg_id))

    return {"status": "OPENING", "box_label": box["label"], "reservation_id": reservation["id"]}


async def _finish_drop(reservation_id: int, box_id: int, box_label: str, customer_tg_id: int) -> None:
    closed = await serial_link.wait_for_door_closed(box_label, timeout=120)
    weight_ok = await serial_link.wait_for_weight_confirm(box_label, timeout=15) if closed else False

    with db.LOCK:
        if closed and weight_ok:
            db.db().execute("UPDATE boxes SET status='STORED' WHERE id=?", (box_id,))
            db.db().execute(
                "UPDATE reservations SET status='STORED', stored_at=? WHERE id=?",
                (db.now_iso(), reservation_id),
            )
        db.db().commit()

    db.log_event(
        "drop_finished" if (closed and weight_ok) else "drop_confirm_failed",
        json.dumps({"reservation_id": reservation_id, "closed": closed, "weight_ok": weight_ok}),
    )
    if closed and weight_ok:
        telegram_push.push_to_customer(customer_tg_id, f"📦 ของถึงแล้ว! เก็บไว้ที่กล่อง {box_label}")
