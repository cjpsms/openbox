#!/usr/bin/env python3
"""
customer-bot (openbox smart locker — customer side)

Thin Telegram client over openbox-api (~/program/openbox/openbox-api),
which owns the DB, Claude vision calls, and SlipOK verification. Flow:

    0. Unregistered users register: photo of student ID -> Claude vision
       (via the api) extracts name + code -> confirm -> registered.
    1. Registered + no pending reservation: send an order-screenshot photo
       -> api extracts platform/order id/recipient -> confirm -> pick a
       box size -> api creates a reservation (AWAITING_PAYMENT) with the
       rent amount due.
    2. Registered + has a pending AWAITING_PAYMENT reservation: the next
       photo is treated as the payment slip -> api verifies via SlipOK ->
       on success the reservation moves to WAITING_DROP and the customer
       waits for a rider to drop the item (openbox-api pushes a Telegram
       message itself once that happens — see telegram_push.py in the
       api — this bot doesn't need to poll for it).
    3. /pickup once the api shows a STORED reservation: opens the box
       (api checks cam1 presence first) — completion is pushed by the
       api the same way.

No token is hardcoded. It is read from ../.env (see ../.env.example for
the expected keys) - shared with the other openbox sub-projects.
"""

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
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

PROCESSING_FRAME_INTERVAL_SECONDS = 0.4
DOTS_CYCLE = ["", ".", "..", "..."]

BOX_LABELS = {
    "large": "กล่องขนาด 20x30x30cm",
    "small": "กล่องขนาด 15x20x20cm",
}

# In-memory, transient — same pattern as the old demo's pending_registrations.
# user_id -> {"name": ..., "student_code": ...}
pending_registrations: dict[int, dict] = {}
# user_id -> extracted order dict, awaiting confirm + box-size pick
pending_orders: dict[int, dict] = {}


async def edit_text_safe(editable, text: str) -> None:
    """Edit a Message or CallbackQuery's text, ignoring "not modified"."""
    edit = editable.edit_text if hasattr(editable, "edit_text") else editable.edit_message_text
    try:
        await edit(text)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


async def animate_processing(status_message, seconds: float) -> None:
    elapsed = 0.0
    frame = 0
    while elapsed < seconds:
        await asyncio.sleep(PROCESSING_FRAME_INTERVAL_SECONDS)
        elapsed += PROCESSING_FRAME_INTERVAL_SECONDS
        frame = (frame + 1) % len(DOTS_CYCLE)
        await edit_text_safe(status_message, f"⏳ Processing{DOTS_CYCLE[frame]}")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: check this user_id's registration status (via the api) first."""
    if update.message is None or update.effective_user is None:
        return
    existing = await api_client.get_customer(update.effective_user.id)
    if existing is not None:
        await update.message.reply_text(
            f"ยินดีต้อนรับกลับมา, {existing['name']} 👋\nส่งรูปสกรีนช็อตออเดอร์เพื่อเริ่มใช้งานได้เลย"
        )
    else:
        await update.message.reply_text(
            "กรุณาลงทะเบียน / สมัครสมาชิกก่อนใช้งาน\nส่งรูปบัตรนักเรียนของคุณมาเพื่อลงทะเบียน"
        )


async def handle_pickup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    reservation = await api_client.get_stored_reservation(update.effective_user.id)
    if reservation is None:
        await update.message.reply_text("ยังไม่พบพัสดุที่พร้อมให้รับตอนนี้")
        return
    keyboard = [[InlineKeyboardButton("📥 รับของ", callback_data=f"pickup_{reservation['id']}")]]
    await update.message.reply_text(
        "พบพัสดุพร้อมรับ 1 ชิ้น กดปุ่มด้านล่างเมื่อยืนอยู่หน้าตู้แล้ว",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pickup_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    reservation_id = int(query.data.split("_", 1)[1])
    try:
        await api_client.request_pickup(reservation_id, update.effective_user.id)
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ เปิดตู้ไม่สำเร็จ: {e.detail}")
        return
    await edit_text_safe(query, "🔓 กำลังเปิดตู้... หยิบของแล้วปิดประตูให้สนิท")


# ---------------------------------------------------------- registration

async def handle_id_card_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registration step: photo in -> Claude vision (via api) -> confirm."""
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    status = await update.message.reply_text("⏳ กำลังตรวจสอบบัตรนักเรียน...")

    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = str(Path(tmp_dir) / "student_id.jpg")
        await tg_file.download_to_drive(image_path)
        try:
            result = await api_client.verify_student_id(image_path)
        except api_client.ApiError as e:
            logger.error("student ID extraction failed: %s", e)
            await edit_text_safe(status, "⚠️ ตรวจสอบบัตรไม่สำเร็จ (ระบบขัดข้อง) กรุณาลองส่งรูปใหม่อีกครั้ง")
            return

    if not result.get("is_student_id") or not result.get("name") or not result.get("student_code"):
        reason = result.get("reason") or "ไม่พบข้อมูลบัตรนักเรียนที่ชัดเจนในรูปนี้"
        await edit_text_safe(status, f"⚠️ {reason}\nกรุณาถ่ายรูปบัตรนักเรียนใหม่ให้ชัดเจน แล้วส่งอีกครั้ง")
        return

    pending_registrations[user_id] = {"name": result["name"], "student_code": result["student_code"]}
    keyboard = [[
        InlineKeyboardButton("✅ ยืนยัน", callback_data="regconfirm"),
        InlineKeyboardButton("🔁 ถ่ายใหม่", callback_data="regretry"),
    ]]
    await edit_text_safe(
        status,
        f"ตรวจพบข้อมูล:\nชื่อ: {result['name']}\nรหัสนักเรียน: {result['student_code']}\n\nถูกต้องไหม?",
    )
    await status.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_reg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    user_id = update.effective_user.id
    pending = pending_registrations.pop(user_id, None)
    if pending is None:
        await edit_text_safe(query, "ไม่พบข้อมูลที่รอยืนยัน กรุณาส่งรูปบัตรนักเรียนใหม่อีกครั้ง")
        return
    try:
        await api_client.register_customer(user_id, pending["name"], pending["student_code"])
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ ลงทะเบียนไม่สำเร็จ: {e.detail}")
        return
    await edit_text_safe(
        query,
        f"ลงทะเบียนสำเร็จ ✅\nชื่อ: {pending['name']}\nรหัสนักเรียน: {pending['student_code']}\n\n"
        "ส่งรูปสกรีนช็อตออเดอร์เพื่อเริ่มใช้งานได้เลย",
    )


async def handle_reg_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    pending_registrations.pop(update.effective_user.id, None)
    await edit_text_safe(query, "กรุณาถ่ายรูปบัตรนักเรียนใหม่อีกครั้ง แล้วส่งเข้ามา")


# --------------------------------------------------------------- order flow

async def handle_order_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id

    status = await update.message.reply_text("⏳ Processing")
    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)

    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = str(Path(tmp_dir) / "order.jpg")
        await tg_file.download_to_drive(image_path)
        animate_task = asyncio.create_task(animate_processing(status, 2.0))
        try:
            result = await api_client.verify_order_screenshot(image_path)
        except api_client.ApiError as e:
            animate_task.cancel()
            await edit_text_safe(status, f"⚠️ อ่านสกรีนช็อตไม่สำเร็จ: {e.detail}")
            return
        await animate_task

    if not result.get("is_order_screenshot"):
        reason = result.get("reason") or "ไม่แน่ใจว่าเป็นสกรีนช็อตออเดอร์"
        await edit_text_safe(status, f"⚠️ {reason}\nกรุณาส่งสกรีนช็อตออเดอร์จากแอปช้อปปิ้งอีกครั้ง")
        return

    pending_orders[user_id] = result
    lines = [
        "ตรวจพบออเดอร์:",
        f"แพลตฟอร์ม: {result.get('platform') or '-'}",
        f"เลขที่ออเดอร์: {result.get('order_id') or '-'}",
        f"ร้าน: {result.get('shop') or '-'}",
        f"ผู้รับ: {result.get('recipient_name') or '-'}",
        "",
        "ถูกต้องไหม?",
    ]
    keyboard = [[
        InlineKeyboardButton("✅ ยืนยัน", callback_data="orderconfirm"),
        InlineKeyboardButton("🔁 ส่งรูปใหม่", callback_data="orderretry"),
    ]]
    await edit_text_safe(status, "\n".join(lines))
    await status.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    if update.effective_user.id not in pending_orders:
        await edit_text_safe(query, "ไม่พบออเดอร์ที่รอยืนยัน กรุณาส่งสกรีนช็อตใหม่อีกครั้ง")
        return
    keyboard = [
        [InlineKeyboardButton(BOX_LABELS["large"], callback_data="boxsize_large")],
        [InlineKeyboardButton(BOX_LABELS["small"], callback_data="boxsize_small")],
    ]
    await edit_text_safe(query, "เลือกขนาดล็อกเกอร์ที่ต้องการ:")
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_order_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    pending_orders.pop(update.effective_user.id, None)
    await edit_text_safe(query, "กรุณาส่งสกรีนช็อตออเดอร์ใหม่อีกครั้ง")


async def handle_box_size_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    user_id = update.effective_user.id
    order = pending_orders.pop(user_id, None)
    if order is None:
        await edit_text_safe(query, "ไม่พบออเดอร์ที่รอยืนยัน กรุณาส่งสกรีนช็อตใหม่อีกครั้ง")
        return

    box_size = query.data.split("_", 1)[1]  # "large" | "small"
    try:
        reservation = await api_client.create_reservation(user_id, box_size, order)
    except api_client.ApiError as e:
        await edit_text_safe(query, f"⚠️ จองไม่สำเร็จ: {e.detail}")
        return

    await edit_text_safe(
        query,
        f"จอง{BOX_LABELS[box_size]}แล้ว ✅\n"
        f"ค่าเช่า: {reservation['amount_due_thb']:.0f} บาท\n\n"
        "กรุณาโอนเงินแล้วส่งรูปสลิปมาที่แชทนี้เพื่อยืนยันการชำระเงิน",
    )


# --------------------------------------------------------------- payment

async def handle_slip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, reservation: dict) -> None:
    if update.message is None:
        return
    status = await update.message.reply_text("⏳ กำลังตรวจสอบสลิป...")
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = str(Path(tmp_dir) / "slip.jpg")
        await tg_file.download_to_drive(image_path)
        try:
            updated = await api_client.pay_reservation(reservation["id"], image_path)
        except api_client.ApiError as e:
            await edit_text_safe(status, f"⚠️ ตรวจสอบสลิปไม่สำเร็จ: {e.detail}\nกรุณาลองส่งสลิปใหม่อีกครั้ง")
            return

    await edit_text_safe(
        status,
        f"ชำระเงินสำเร็จ ✅ (สถานะ: {updated['status']})\n"
        "รอไรเดอร์มาส่งของ จะแจ้งเตือนทันทีที่ของถึง",
    )


# --------------------------------------------------------------- dispatch

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch a photo based on where this user currently is in the flow."""
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id

    customer = await api_client.get_customer(user_id)
    if customer is None:
        await handle_id_card_photo(update, context)
        return

    pending_reservation = await api_client.get_pending_reservation(user_id)
    if pending_reservation is not None:
        await handle_slip_photo(update, context, pending_reservation)
        return

    await handle_order_photo(update, context)


async def handle_other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    customer = await api_client.get_customer(update.effective_user.id)
    if customer is None:
        await update.message.reply_text("กรุณาถ่ายรูปบัตรนักเรียนส่งมาเพื่อลงทะเบียนก่อนใช้งาน")
    else:
        await update.message.reply_text(
            "ส่งรูปสกรีนช็อตออเดอร์เพื่อจองล็อกเกอร์ หรือพิมพ์ /pickup เพื่อรับของ"
        )


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit(
            "ERROR: TELEGRAM_BOT_TOKEN is not set.\n\n"
            f"Copy {ENV_FILE.parent / '.env.example'} to {ENV_FILE} and "
            "fill in TELEGRAM_BOT_TOKEN, or set it in the environment.\n"
        )

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("pickup", handle_pickup))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_reg_confirm, pattern="^regconfirm$"))
    application.add_handler(CallbackQueryHandler(handle_reg_retry, pattern="^regretry$"))
    application.add_handler(CallbackQueryHandler(handle_order_confirm, pattern="^orderconfirm$"))
    application.add_handler(CallbackQueryHandler(handle_order_retry, pattern="^orderretry$"))
    application.add_handler(CallbackQueryHandler(handle_box_size_choice, pattern="^boxsize_"))
    application.add_handler(CallbackQueryHandler(handle_pickup_button, pattern="^pickup_"))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.PHOTO & ~filters.COMMAND, handle_other_messages)
    )

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting bot (polling)... API base: %s", api_client.API_BASE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
