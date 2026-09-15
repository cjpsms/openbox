"""Customer reservation lifecycle: create -> pay (SlipOK) -> pickup."""

import asyncio
import sqlite3

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import camera, config, db, serial_link, slipok, telegram_push

router = APIRouter(prefix="/reservations", tags=["reservations"])


def _save_upload(upload_bytes: bytes, subdir: str, suffix: str = ".jpg") -> str:
    import uuid

    path = config.MEDIA_DIR / subdir / f"{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(upload_bytes)
    return str(path)


def _row_or_404(reservation_id: int) -> sqlite3.Row:
    row = db.db().execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "reservation not found")
    return row


@router.post("")
async def create_reservation(
    tg_user_id: int = Form(...),
    box_size: str = Form(...),
    order_platform: str = Form(None),
    order_id: str = Form(None),
    recipient_name: str = Form(None),
    phone_last4: str = Form(None),
    tracking_no: str = Form(None),
):
    if box_size not in config.BOX_RENT_THB:
        raise HTTPException(400, f"box_size must be one of {list(config.BOX_RENT_THB)}")

    customer = db.db().execute(
        "SELECT * FROM customers WHERE tg_user_id = ?", (tg_user_id,)
    ).fetchone()
    if customer is None:
        raise HTTPException(403, "customer not registered")

    amount_due = config.BOX_RENT_THB[box_size]
    with db.LOCK:
        cur = db.db().execute(
            "INSERT INTO reservations (customer_id, box_size, status, order_platform, "
            "order_id, recipient_name, phone_last4, tracking_no, amount_due_thb, created_at) "
            "VALUES (?, ?, 'AWAITING_PAYMENT', ?, ?, ?, ?, ?, ?, ?)",
            (
                customer["id"], box_size, order_platform, order_id, recipient_name,
                phone_last4, tracking_no, amount_due, db.now_iso(),
            ),
        )
        db.db().commit()
        reservation_id = cur.lastrowid

    db.log_event("reservation_created", f'{{"id": {reservation_id}, "tg_user_id": {tg_user_id}}}')
    return dict(_row_or_404(reservation_id))


@router.get("/{reservation_id}")
async def get_reservation(reservation_id: int):
    return dict(_row_or_404(reservation_id))


@router.get("")
async def list_reservations(tg_user_id: int | None = None, status: str | None = None):
    query = (
        "SELECT r.* FROM reservations r JOIN customers c ON r.customer_id = c.id WHERE 1=1"
    )
    params: list = []
    if tg_user_id is not None:
        query += " AND c.tg_user_id = ?"
        params.append(tg_user_id)
    if status is not None:
        query += " AND r.status = ?"
        params.append(status)
    query += " ORDER BY r.id DESC"
    rows = db.db().execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.post("/{reservation_id}/pay")
async def pay_reservation(reservation_id: int, file: UploadFile = File(...)):
    reservation = _row_or_404(reservation_id)
    if reservation["status"] != "AWAITING_PAYMENT":
        raise HTTPException(409, f"reservation status is {reservation['status']}, not AWAITING_PAYMENT")

    slip_path = _save_upload(await file.read(), "slips", ".jpg")
    status_code, body = slipok.check_slip(slip_path, reservation["amount_due_thb"])

    if status_code != 200 or not slipok.is_success(body):
        raise HTTPException(400, f"slip rejected by SlipOK: {body}")

    amount = slipok.slip_amount(body)
    trans_ref = slipok.slip_trans_ref(body)
    if amount is None or amount < reservation["amount_due_thb"]:
        raise HTTPException(400, f"slip amount {amount} is less than amount due {reservation['amount_due_thb']}")

    try:
        with db.LOCK:
            db.db().execute(
                "INSERT INTO payments (reservation_id, amount_thb, trans_ref, verified_at) "
                "VALUES (?, ?, ?, ?)",
                (reservation_id, amount, trans_ref, db.now_iso()),
            )
            db.db().execute(
                "UPDATE reservations SET status='WAITING_DROP', paid_at=? WHERE id=?",
                (db.now_iso(), reservation_id),
            )
            db.db().commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "this slip (transaction ref) has already been used")

    db.log_event("reservation_paid", f'{{"id": {reservation_id}, "trans_ref": "{trans_ref}"}}')
    return dict(_row_or_404(reservation_id))


@router.post("/{reservation_id}/pickup-request")
async def request_pickup(reservation_id: int, tg_user_id: int = Form(...)):
    reservation = _row_or_404(reservation_id)
    if reservation["status"] != "STORED":
        raise HTTPException(409, f"reservation status is {reservation['status']}, not STORED")
    if not camera.is_present():
        raise HTTPException(
            409, "ไม่พบคนอยู่หน้าตู้ — กรุณายืนหน้าตู้แล้วลองใหม่ (cam1 ไม่เห็นความเคลื่อนไหว)"
        )

    box = db.db().execute("SELECT * FROM boxes WHERE id = ?", (reservation["box_id"],)).fetchone()
    if box is None:
        raise HTTPException(500, "reservation has no assigned box")

    with db.LOCK:
        db.db().execute("UPDATE boxes SET status='OPEN_FOR_PICKUP' WHERE id=?", (box["id"],))
        db.db().execute("UPDATE reservations SET status='OPEN_FOR_PICKUP' WHERE id=?", (reservation_id,))
        db.db().commit()

    await serial_link.open_box(box["label"])
    db.log_event("pickup_opened", f'{{"reservation_id": {reservation_id}, "box": "{box["label"]}"}}')

    asyncio.create_task(_finish_pickup(reservation_id, box["id"], box["label"], tg_user_id))
    return {"status": "OPENING", "box_label": box["label"]}


async def _finish_pickup(reservation_id: int, box_id: int, box_label: str, tg_user_id: int) -> None:
    closed = await serial_link.wait_for_door_closed(box_label, timeout=120)
    with db.LOCK:
        if closed:
            db.db().execute("UPDATE boxes SET status='FREE' WHERE id=?", (box_id,))
            db.db().execute(
                "UPDATE reservations SET status='DONE', picked_up_at=? WHERE id=?",
                (db.now_iso(), reservation_id),
            )
        else:
            db.db().execute(
                "UPDATE reservations SET status='STORED' WHERE id=?", (reservation_id,)
            )  # revert — door never confirmed closed, box may still hold the item
        db.db().commit()

    db.log_event(
        "pickup_finished" if closed else "pickup_door_timeout",
        f'{{"reservation_id": {reservation_id}}}',
    )
    if closed:
        telegram_push.push_to_customer(tg_user_id, "✅ รับของเรียบร้อยแล้ว ขอบคุณที่ใช้บริการ")
