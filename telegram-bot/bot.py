#!/usr/bin/env python3
"""
telegram-bot demo (smart locker mockup)

This is a throwaway UI/flow demo, NOT production code. It does not verify
payment or decode any QR content — it just shows the intended chat flow:

    0. First-time users must register: send a photo of their student ID
       card. The bot extracts name + student code (see registration.py)
       and asks for confirmation before saving the account.
    1. Once registered, a photo (order screenshot) starts the box flow:
       bot replies "⏳ Processing..." and shows the "typing..." chat action.
    2. Bot waits (PROCESSING_DELAY_SECONDS, stand-in for real processing).
    3. Bot edits that message into an inline keyboard (one button per box
       size) to pick a locker box.
    4. On button press, bot edits the message to "sending to shelf..." then,
       after a short beat, "data updated, waiting to receive item" (also
       just messages, no real hardware/API call).

No token is hardcoded. It is read from ../.env (see ../.env.example for the
expected keys) - shared with the other openbox sub-projects.
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

import registration

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

PROCESSING_DELAY_SECONDS = 2

# callback_data -> label, so the confirmation message can look it up
BOX_LABELS = {
    "box_1": "กล่องขนาด 20x30x30cm",
    "box_2": "กล่องขนาด 15x20x20cm",
}


PROCESSING_FRAME_INTERVAL_SECONDS = 0.4
DOTS_CYCLE = ["", ".", "..", "..."]

# user_id -> {"name": ..., "student_code": ...}, awaiting ยืนยัน/ถ่ายใหม่.
# In-memory only (demo) - lost on restart, same as the rest of this mockup's
# transient state.
pending_registrations: dict[int, dict] = {}


async def edit_text_safe(editable, text: str) -> None:
    """Edit a Message or CallbackQuery's text, ignoring "not modified"."""
    edit = editable.edit_text if hasattr(editable, "edit_text") else editable.edit_message_text
    try:
        await edit(text)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: check this user_id's registration status first, then greet."""
    if update.message is None or update.effective_user is None:
        return

    existing = registration.get_registration(update.effective_user.id)
    if existing is not None:
        await update.message.reply_text(
            f"ยินดีต้อนรับกลับมา, {existing['name']} 👋\nส่งรูปสกรีนช็อตออเดอร์เพื่อเริ่มใช้งานได้เลย"
        )
    else:
        await update.message.reply_text(
            "กรุณาลงทะเบียน / สมัครสมาชิกก่อนใช้งาน\nส่งรูปบัตรนักเรียนของคุณมาเพื่อลงทะเบียน"
        )


async def handle_id_card_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registration step: photo in -> Claude vision extraction -> confirm."""
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
            result = await registration.extract_student_id(image_path)
        except RuntimeError as e:
            logger.error("student ID extraction failed: %s", e)
            await edit_text_safe(
                status,
                "⚠️ ตรวจสอบบัตรไม่สำเร็จ (ระบบขัดข้อง) กรุณาลองส่งรูปใหม่อีกครั้ง",
            )
            return

    if not result["is_student_id"] or not result["name"] or not result["student_code"]:
        reason = result["reason"] or "ไม่พบข้อมูลบัตรนักเรียนที่ชัดเจนในรูปนี้"
        await edit_text_safe(status, f"⚠️ {reason}\nกรุณาถ่ายรูปบัตรนักเรียนใหม่ให้ชัดเจน แล้วส่งอีกครั้ง")
        return

    pending_registrations[user_id] = {
        "name": result["name"],
        "student_code": result["student_code"],
    }
    keyboard = [
        [
            InlineKeyboardButton("✅ ยืนยัน", callback_data="regconfirm"),
            InlineKeyboardButton("🔁 ถ่ายใหม่", callback_data="regretry"),
        ]
    ]
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

    registration.register(user_id, pending["name"], pending["student_code"])
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch a photo: unregistered users register first, else box flow."""
    if update.message is None or update.effective_user is None:
        return

    if not registration.is_registered(update.effective_user.id):
        await handle_id_card_photo(update, context)
        return

    await handle_order_photo(update, context)


async def handle_order_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1-4: photo in -> animated 'Processing...' + 'typing' -> box picker."""
    if update.message is None:
        return

    # Two processing indicators, belt-and-suspenders: the "typing..." chat
    # action (shows in the header, if the client renders it) plus a real,
    # animated "⏳ Processing..." message (guaranteed visible everywhere),
    # which gets edited in place into the box picker below (stays one
    # message throughout).
    status = await update.message.reply_text("⏳ Processing")
    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)

    # Simulated processing delay (e.g. would-be QR decode / payment check),
    # animated by cycling the trailing dots every ~0.4s.
    elapsed = 0.0
    frame = 0
    while elapsed < PROCESSING_DELAY_SECONDS:
        await asyncio.sleep(PROCESSING_FRAME_INTERVAL_SECONDS)
        elapsed += PROCESSING_FRAME_INTERVAL_SECONDS
        frame = (frame + 1) % len(DOTS_CYCLE)
        await edit_text_safe(status, f"⏳ Processing{DOTS_CYCLE[frame]}")

    keyboard = [
        [InlineKeyboardButton(BOX_LABELS["box_1"], callback_data="box_1")],
        [InlineKeyboardButton(BOX_LABELS["box_2"], callback_data="box_2")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await status.edit_text(
        "เลือกล็อกเกอร์ที่ต้องการ:",
        reply_markup=reply_markup,
    )


async def handle_box_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 5: user tapped a box button -> sending-to-shelf -> waiting for item."""
    query = update.callback_query
    if query is None:
        return

    await query.answer()  # ack the tap so Telegram stops the loading spinner

    label = BOX_LABELS.get(query.data, query.data)
    await edit_text_safe(query, f"เลือก{label}แล้ว\n📤 กำลังส่งข้อมูลไปยังชั้นสินค้า...")
    await asyncio.sleep(0.5)
    await edit_text_safe(query, f"เลือก{label}แล้ว\n✅ อัปเดตข้อมูลแล้ว กำลังรอรับของ")


async def handle_other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anything that isn't a photo - nudge the user toward the demo flow."""
    if update.message is None or update.effective_user is None:
        return
    if registration.is_registered(update.effective_user.id):
        await update.message.reply_text("ส่งรูปภาพมาเพื่อเริ่มต้น (demo: send a photo to start)")
    else:
        await update.message.reply_text("กรุณาถ่ายรูปบัตรนักเรียนส่งมาเพื่อลงทะเบียนก่อนใช้งาน")


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
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_reg_confirm, pattern="^regconfirm$"))
    application.add_handler(CallbackQueryHandler(handle_reg_retry, pattern="^regretry$"))
    application.add_handler(CallbackQueryHandler(handle_box_choice, pattern="^box_"))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.PHOTO & ~filters.COMMAND, handle_other_messages)
    )

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting bot (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
