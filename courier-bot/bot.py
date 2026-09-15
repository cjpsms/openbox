#!/usr/bin/env python3
"""
rider-bot (openbox smart locker — courier/delivery side)

Thin Telegram client over openbox-api (~/program/openbox/openbox-api),
which owns the DB, Claude vision calls, and the drop-off matching logic.
Flow:

    0. Registration: type name -> confirm -> share phone number (Telegram
       contact button, verifies the number is real) -> send a photo of
       your employee/courier ID card -> Claude vision (via the api)
       pre-screens it for plausibility. If it passes, status becomes
       PENDING_APPROVAL.
    1. Approval: STOPGAP for now — there's no admin bot yet (see LOG), so
       cj approves manually (POST /riders/{tg_user_id}/approve, e.g. via
       curl) after eyeballing the card. Once approved, openbox-api pushes
       a Telegram message to the rider itself.
    2. Drop-off: an APPROVED rider sends /drop (or the "📦 ส่งของ" text) to
       arm the scanner, then presses the parcel label against it. The bot
       polls the drop session until Claude vision has matched it to a
       customer's reservation, then the rider confirms and the box opens.

No token is hardcoded. It is read from ../.env (see ../.env.example for
the expected key, COURIER_BOT_TOKEN) - shared with the other openbox
sub-projects.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import api_client

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file() -> None:
    """Load KEY=VALUE lines from ../.env into os.environ, if it exists."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_env_file()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DROP_SESSION_POLL_SECONDS = 2.0
DROP_SESSION_POLL_MAX_SECONDS = 300.0

# In-memory, transient (same pattern the demo bots have always used).
pending_registrations: dict[int, str] = {}  # user_id -> candidate name


async def edit_text_safe(editable, text: str) -> None:
    edit = editable.edit_text if hasattr(editable, "edit_text") else editable.edit_message_text
    try:
        await edit(text)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


STATUS_MESSAGES = {
    "NEW": "กรุณาส่งรูปบัตรพนักงาน/บัตรไรเดอร์เพื่อยืนยันตัวตน",
    "PENDING_APPROVAL": "⏳ บัญชีของคุณกำลังรอการอนุมัติจากแอดมิน กรุณารอสักครู่",
    "REJECTED": "❌ บัญชีไม่ผ่านการอนุมัติ",
    "APPROVED": None,  # handled specially
}


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    rider = await api_client.get_rider(update.effective_user.id)
    if rider is None:
        await update.message.reply_text(
            "กรุณาลงทะเบียน / สมัครสมาชิกก่อนใช้งาน\nพิมพ์ชื่อ-นามสกุลของคุณส่งมาเพื่อลงทะเบียน"
        )
        return

    if rider["status"] == "APPROVED":
        await update.message.reply_text(
            f"ยินดีต้อนรับกลับมา, {rider['name']} 👋\nพิมพ์ /drop เมื่อพร้อมส่งของ"
        )
        return

    if not rider.get("phone"):
        await _ask_for_phone(update)
        return

    text = STATUS_MESSAGES.get(rider["status"], f"สถานะ: {rider['status']}")
    if rider["status"] == "REJECTED" and rider.get("card_reason"):
        text += f"\nเหตุผล: {rider['card_reason']}\nส่งรูปบัตรใหม่เพื่อยื่นขออนุมัติอีกครั้ง"
    await update.message.reply_text(text)


async def _ask_for_phone(update: Update) -> None:
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 แชร์เบอร์โทรศัพท์", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text("กรุณากดปุ่มด้านล่างเพื่อแชร์เบอร์โทรศัพท์ของคุณ", reply_markup=keyboard)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None or update.message.text is None:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text in ("/drop", "📦 ส่งของ", "ส่งของ"):
        await handle_drop_start(update, context)
        return

    rider = await api_client.get_rider(user_id)
    if rider is not None and rider["status"] == "APPROVED":
        await update.message.reply_text("พิมพ์ /drop เมื่อพร้อมส่งของ")
        return

    # Treat as a name for registration / re-registration (e.g. after REJECTED)
    pending_registrations[user_id] = text
    keyboard = [[
        InlineKeyboardButton("✅ ยืนยัน", callback_data="regconfirm"),
        InlineKeyboardButton("🔁 พิมพ์ใหม่", callback_data="regretry"),
    ]]
    await update.message.reply_text(f"ชื่อ: {text}\nถูกต้องไหม?", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_reg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    user_id = update.effective_user.id
    name = pending_registrations.pop(user_id, None)
    if name is None:
        await edit_text_safe(query, "ไม่พบข้อมูลที่รอยืนยัน กรุณาพิมพ์ชื่อใหม่อีกครั้ง")
        return
    try:
        await api_client.register_rider(user_id, name)
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ ลงทะเบียนไม่สำเร็จ: {e.detail}")
        return
    await edit_text_safe(query, f"บันทึกชื่อแล้ว ✅\nชื่อ: {name}")
    await _ask_for_phone(update)


async def handle_reg_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    pending_registrations.pop(update.effective_user.id, None)
    await edit_text_safe(query, "กรุณาพิมพ์ชื่อ-นามสกุลของคุณใหม่อีกครั้ง")


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rider tapped the phone-share button."""
    if update.message is None or update.effective_user is None or update.message.contact is None:
        return
    user_id = update.effective_user.id
    contact = update.message.contact
    if contact.user_id is not None and contact.user_id != user_id:
        await update.message.reply_text(
            "กรุณาแชร์เบอร์โทรศัพท์ของคุณเอง ไม่ใช่ของผู้อื่น", reply_markup=ReplyKeyboardRemove()
        )
        return

    rider = await api_client.get_rider(user_id)
    name = rider["name"] if rider else "-"
    try:
        await api_client.register_rider(user_id, name, phone=contact.phone_number)
    except api_client.ApiError as e:
        await update.message.reply_text(f"⚠️ บันทึกเบอร์ไม่สำเร็จ: {e.detail}", reply_markup=ReplyKeyboardRemove())
        return

    await update.message.reply_text(
        "บันทึกเบอร์โทรศัพท์แล้ว ✅\nขั้นตอนสุดท้าย: กรุณาส่งรูปบัตรพนักงาน/บัตรไรเดอร์ของคุณมาที่แชทนี้",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Employee-card photo -> Claude vision pre-screen (via the api)."""
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    rider = await api_client.get_rider(user_id)
    if rider is None:
        await update.message.reply_text("กรุณาลงทะเบียนก่อน พิมพ์ชื่อ-นามสกุลของคุณส่งมา")
        return
    if rider["status"] == "APPROVED":
        await update.message.reply_text("บัญชีของคุณอนุมัติแล้ว ไม่ต้องส่งรูปบัตรอีก พิมพ์ /drop เพื่อส่งของ")
        return

    status = await update.message.reply_text("⏳ กำลังตรวจสอบบัตร...")
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = str(Path(tmp_dir) / "card.jpg")
        await tg_file.download_to_drive(image_path)
        try:
            result = await api_client.submit_card(user_id, image_path)
        except api_client.ApiError as e:
            await edit_text_safe(status, f"⚠️ ตรวจสอบบัตรไม่สำเร็จ (ระบบขัดข้อง): {e.detail}")
            return

    if result["passed"]:
        await edit_text_safe(
            status,
            "ตรวจสอบบัตรผ่านเบื้องต้นแล้ว ✅\n⏳ กำลังรอแอดมินอนุมัติ จะแจ้งเตือนทันทีที่อนุมัติ",
        )
    else:
        reasons = "\n".join(f"• {r}" for r in result["reasons"])
        await edit_text_safe(status, f"⚠️ บัตรไม่ผ่านการตรวจสอบเบื้องต้น:\n{reasons}\n\nกรุณาถ่ายรูปใหม่แล้วส่งอีกครั้ง")


# --------------------------------------------------------------- drop flow

async def handle_drop_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    try:
        session = await api_client.start_drop_session(user_id)
    except api_client.ApiError as e:
        await update.message.reply_text(f"⚠️ เริ่มส่งของไม่สำเร็จ: {e.detail}")
        return

    status = await update.message.reply_text(
        "🔦 สแกนเนอร์พร้อมแล้ว กรุณานำฉลากพัสดุไปแปะที่สแกนเนอร์"
    )
    asyncio.create_task(_poll_drop_session(status, session["id"], user_id))


async def _poll_drop_session(status_message, session_id: int, rider_tg_id: int) -> None:
    elapsed = 0.0
    while elapsed < DROP_SESSION_POLL_MAX_SECONDS:
        await asyncio.sleep(DROP_SESSION_POLL_SECONDS)
        elapsed += DROP_SESSION_POLL_SECONDS
        try:
            session = await api_client.get_drop_session(session_id)
        except api_client.ApiError:
            continue

        state = session["status"]
        if state == "OPEN":
            continue
        if state == "MATCHED":
            reservation = await api_client.get_reservation(session["reservation_id"])
            masked_name = _mask_name(reservation.get("recipient_name"))
            keyboard = [[InlineKeyboardButton("✅ ยืนยัน ใช่พัสดุนี้", callback_data=f"dropconfirm_{session_id}")]]
            await edit_text_safe(
                status_message,
                f"พบออเดอร์ที่ตรงกัน: {masked_name} (เลขที่ {reservation.get('order_id') or '-'})\nยืนยันหรือไม่?",
            )
            await status_message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            return
        if state == "MULTI_MATCH":
            import json

            candidate_ids = json.loads(session["note"] or "[]")
            buttons = []
            for rid in candidate_ids:
                reservation = await api_client.get_reservation(rid)
                buttons.append([InlineKeyboardButton(
                    _mask_name(reservation.get("recipient_name")), callback_data=f"droppick_{session_id}_{rid}"
                )])
            await edit_text_safe(status_message, "พบหลายออเดอร์ที่ตรงกัน กรุณาเลือก:")
            await status_message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
            return
        if state == "NO_MATCH":
            await edit_text_safe(status_message, "⚠️ ไม่พบออเดอร์ที่ตรงกับพัสดุนี้ กรุณาตรวจสอบอีกครั้งหรือติดต่อแอดมิน")
            return
        if state == "EXPIRED":
            await edit_text_safe(status_message, "⌛ หมดเวลา ไม่พบพัสดุมาแปะที่สแกนเนอร์")
            return
        if state == "ERROR":
            await edit_text_safe(status_message, f"⚠️ เกิดข้อผิดพลาด: {session.get('note') or 'ไม่ทราบสาเหตุ'}")
            return

    await edit_text_safe(status_message, "⌛ หมดเวลารอ กรุณาลอง /drop ใหม่อีกครั้ง")


def _mask_name(name: str | None) -> str:
    if not name:
        return "(ไม่ทราบชื่อ)"
    parts = name.split()
    return " ".join(p[0] + "***" if len(p) > 1 else p for p in parts)


async def handle_drop_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    session_id = int(query.data.split("_", 1)[1])
    try:
        result = await api_client.confirm_drop(session_id)
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ ยืนยันไม่สำเร็จ: {e.detail}")
        return
    await edit_text_safe(query, f"🔓 กำลังเปิด{result['box_label']}... ใส่ของแล้วปิดประตูให้สนิท")


async def handle_drop_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    _, session_id, reservation_id = query.data.split("_")
    try:
        await api_client.pick_candidate(int(session_id), int(reservation_id))
        result = await api_client.confirm_drop(int(session_id))
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ ไม่สำเร็จ: {e.detail}")
        return
    await edit_text_safe(query, f"🔓 กำลังเปิด{result['box_label']}... ใส่ของแล้วปิดประตูให้สนิท")


def build_application() -> Application:
    token = os.environ.get("COURIER_BOT_TOKEN")
    if not token:
        sys.exit(
            "ERROR: COURIER_BOT_TOKEN is not set.\n\n"
            f"Copy {ENV_FILE.parent / '.env.example'} to {ENV_FILE} and "
            "fill in COURIER_BOT_TOKEN, or set it in the environment.\n"
        )

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("drop", handle_drop_start))
    application.add_handler(CallbackQueryHandler(handle_reg_confirm, pattern="^regconfirm$"))
    application.add_handler(CallbackQueryHandler(handle_reg_retry, pattern="^regretry$"))
    application.add_handler(CallbackQueryHandler(handle_drop_confirm, pattern="^dropconfirm_"))
    application.add_handler(CallbackQueryHandler(handle_drop_pick, pattern="^droppick_"))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting rider-bot (polling)... API base: %s", api_client.API_BASE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
