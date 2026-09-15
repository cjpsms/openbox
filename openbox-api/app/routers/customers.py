"""Customer registration + order-screenshot verification."""

import sqlite3
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config, db, vision

router = APIRouter(prefix="/customers", tags=["customers"])


def _save_upload(upload_bytes: bytes, subdir: str, suffix: str = ".jpg") -> str:
    path = config.MEDIA_DIR / subdir / f"{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(upload_bytes)
    return str(path)


@router.post("/verify-id")
async def verify_student_id(file: UploadFile = File(...)):
    """Run the uploaded student-ID photo through Claude vision. Does NOT
    register anyone — the bot calls /customers/register after the user
    confirms the extracted name/code."""
    image_path = _save_upload(await file.read(), "order_screenshots", ".jpg")
    try:
        result = await vision.extract_json(image_path, vision.STUDENT_ID_PROMPT)
    except RuntimeError as e:
        raise HTTPException(502, f"vision extraction failed: {e}")
    result["image_path"] = image_path
    return result


@router.post("/register")
async def register_customer(tg_user_id: int = Form(...), name: str = Form(...), student_code: str = Form(...)):
    conn = db.db()
    try:
        with db.LOCK:
            conn.execute(
                "INSERT INTO customers (tg_user_id, name, student_code, registered_at) "
                "VALUES (?, ?, ?, ?)",
                (tg_user_id, name, student_code, db.now_iso()),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "customer already registered")
    db.log_event("customer_registered", f'{{"tg_user_id": {tg_user_id}}}')
    return await get_customer(tg_user_id)


@router.get("/{tg_user_id}")
async def get_customer(tg_user_id: int):
    row = db.db().execute("SELECT * FROM customers WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not registered")
    return dict(row)


@router.post("/orders/verify")
async def verify_order_screenshot(file: UploadFile = File(...)):
    """Extract order info from a shopping-app screenshot. Does NOT create
    a reservation — the bot calls POST /reservations after confirmation."""
    image_path = _save_upload(await file.read(), "order_screenshots", ".jpg")
    try:
        result = await vision.extract_json(image_path, vision.ORDER_SCREENSHOT_PROMPT)
    except RuntimeError as e:
        raise HTTPException(502, f"vision extraction failed: {e}")
    result["image_path"] = image_path
    return result
