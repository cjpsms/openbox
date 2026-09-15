"""
Rider registration: name + phone, then an employee-card photo that
Claude vision pre-screens for plausibility, then a stopgap manual
approve/reject (the real admin Telegram-bot flow is a separate,
not-yet-built piece — see LOG. For now cj approves directly via
POST /riders/{tg_user_id}/approve, e.g. with curl, until that bot exists).
"""

import json
import sqlite3
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config, db, matching, telegram_push, vision

router = APIRouter(prefix="/riders", tags=["riders"])


def _save_upload(upload_bytes: bytes, subdir: str, suffix: str = ".jpg") -> str:
    path = config.MEDIA_DIR / subdir / f"{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(upload_bytes)
    return str(path)


def _row_or_404(tg_user_id: int) -> sqlite3.Row:
    row = db.db().execute("SELECT * FROM riders WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "rider not registered")
    return row


@router.post("/register")
async def register_rider(tg_user_id: int = Form(...), name: str = Form(...), phone: str = Form(None)):
    conn = db.db()
    existing = conn.execute("SELECT * FROM riders WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
    with db.LOCK:
        if existing is None:
            conn.execute(
                "INSERT INTO riders (tg_user_id, name, phone, status, registered_at) "
                "VALUES (?, ?, ?, 'NEW', ?)",
                (tg_user_id, name, phone, db.now_iso()),
            )
        else:
            conn.execute(
                "UPDATE riders SET name = ?, phone = COALESCE(?, phone) WHERE tg_user_id = ?",
                (name, phone, tg_user_id),
            )
        conn.commit()
    db.log_event("rider_registered", f'{{"tg_user_id": {tg_user_id}}}')
    return dict(_row_or_404(tg_user_id))


@router.post("/{tg_user_id}/card")
async def submit_employee_card(tg_user_id: int, file: UploadFile = File(...)):
    rider = _row_or_404(tg_user_id)
    image_path = _save_upload(await file.read(), "rider_cards", ".jpg")

    try:
        result = await vision.extract_json(image_path, vision.EMPLOYEE_CARD_PROMPT)
    except RuntimeError as e:
        raise HTTPException(502, f"vision extraction failed: {e}")

    is_plausible = bool(result.get("is_employee_card"))
    confidence = float(result.get("confidence") or 0.0)
    company = result.get("company") or ""
    card_name = result.get("name") or ""

    company_ok = any(
        matching.name_similarity(company, allowed) >= 0.5 or allowed.lower() in company.lower()
        for allowed in config.RIDER_ALLOWED_COMPANIES
    ) if company else False
    name_ok = matching.names_match(card_name, rider["name"], threshold=0.55)

    passed = is_plausible and confidence >= 0.7 and company_ok and name_ok

    reasons = []
    if not is_plausible:
        reasons.append(result.get("reason") or "ไม่เหมือนบัตรพนักงานจริง")
    if is_plausible and confidence < 0.7:
        reasons.append(f"ความมั่นใจต่ำ ({confidence:.2f})")
    if is_plausible and not company_ok:
        reasons.append(f"บริษัท '{company}' ไม่อยู่ในรายชื่อที่รองรับ")
    if is_plausible and not name_ok:
        reasons.append(f"ชื่อบนบัตร ('{card_name}') ไม่ตรงกับชื่อที่ลงทะเบียน ('{rider['name']}')")
    reasons.extend(result.get("red_flags") or [])

    with db.LOCK:
        if passed:
            db.db().execute(
                "UPDATE riders SET status='PENDING_APPROVAL', company=?, employee_id=?, "
                "card_reason=NULL, card_photo_path=?, card_checked_at=? WHERE tg_user_id=?",
                (company, result.get("employee_id"), image_path, db.now_iso(), tg_user_id),
            )
        else:
            db.db().execute(
                "UPDATE riders SET card_reason=?, card_checked_at=? WHERE tg_user_id=?",
                ("; ".join(reasons), db.now_iso(), tg_user_id),
            )
        db.db().commit()

    db.log_event(
        "rider_card_checked",
        json.dumps({"tg_user_id": tg_user_id, "passed": passed, "confidence": confidence}),
    )
    return {"passed": passed, "reasons": reasons, "extracted": result}


@router.post("/{tg_user_id}/approve")
async def approve_rider(tg_user_id: int):
    """STOPGAP until the admin bot exists — call this directly (e.g. curl)
    once cj has eyeballed the rider's card. Deletes the stored card photo
    once approved (see design LOG: keep it only until a decision is made)."""
    rider = _row_or_404(tg_user_id)
    if rider["status"] != "PENDING_APPROVAL":
        raise HTTPException(409, f"rider status is {rider['status']}, not PENDING_APPROVAL")

    with db.LOCK:
        db.db().execute(
            "UPDATE riders SET status='APPROVED', approved_at=? WHERE tg_user_id=?",
            (db.now_iso(), tg_user_id),
        )
        db.db().commit()

    if rider["card_photo_path"]:
        from pathlib import Path

        Path(rider["card_photo_path"]).unlink(missing_ok=True)

    db.log_event("rider_approved", f'{{"tg_user_id": {tg_user_id}}}')
    telegram_push.push_to_rider(tg_user_id, "✅ บัญชีไรเดอร์ของคุณได้รับการอนุมัติแล้ว พร้อมใช้งาน")
    return dict(_row_or_404(tg_user_id))


@router.post("/{tg_user_id}/reject")
async def reject_rider(tg_user_id: int, reason: str = Form("")):
    rider = _row_or_404(tg_user_id)
    with db.LOCK:
        db.db().execute(
            "UPDATE riders SET status='REJECTED', card_reason=? WHERE tg_user_id=?",
            (reason, tg_user_id),
        )
        db.db().commit()
    if rider["card_photo_path"]:
        from pathlib import Path

        Path(rider["card_photo_path"]).unlink(missing_ok=True)
    db.log_event("rider_rejected", f'{{"tg_user_id": {tg_user_id}}}')
    telegram_push.push_to_rider(tg_user_id, f"❌ บัญชีไรเดอร์ไม่ผ่านการอนุมัติ: {reason or 'ไม่ระบุเหตุผล'}")
    return dict(_row_or_404(tg_user_id))


@router.get("/{tg_user_id}")
async def get_rider(tg_user_id: int):
    return dict(_row_or_404(tg_user_id))
