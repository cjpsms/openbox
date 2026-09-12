#!/usr/bin/env python3
"""
courier-bot demo (smart locker mockup, delivery/courier side)

Mirrors [[telegram-bot]]'s mockup style but for the courier who drops off
a package, not the customer picking it up. Deliberately minimal for now
("เริ่มให้แบบ สมัคด้วยชื่อ หรืออะไรระบบค่อยทำ" - start with name-only
registration, build the rest as requirements firm up):

    0. Registration: courier sends their name as a text message, confirms
       it, done. No ID/vision extraction like the customer bot.
    1. Everything else (drop-off flow, notifying which box, etc.) is not
       built yet - registered couriers just get a placeholder reply.

No token is hardcoded. It is read from ../.env (see ../.env.example for the
expected key, COURIER_BOT_TOKEN) - shared with the other openbox
sub-projects.
"""

import logging
import os
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# user_id -> candidate name, awaiting ยืนยัน/พิมพ์ใหม่. In-memory only (demo).
pending_registrations: dict[int, str] = {}


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
        await update.message.reply_text(f"ยินดีต้อนรับกลับมา, {existing['name']} 👋")
    else:
        await update.message.reply_text(
            "กรุณาลงทะเบียน / สมัครสมาชิกก่อนใช้งาน\nพิมพ์ชื่อ-นามสกุลของคุณส่งมาเพื่อลงทะเบียน"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any non-command text: registration-name intake, else placeholder."""
    if update.message is None or update.effective_user is None or update.message.text is None:
        return
    user_id = update.effective_user.id

    if registration.is_registered(user_id):
        await update.message.reply_text(
            "ได้รับข้อความแล้ว (ยังไม่ได้ทำฟีเจอร์ส่งของ - รอเพิ่มทีหลัง)"
        )
        return

    name = update.message.text.strip()
    pending_registrations[user_id] = name
    keyboard = [
        [
            InlineKeyboardButton("✅ ยืนยัน", callback_data="regconfirm"),
            InlineKeyboardButton("🔁 พิมพ์ใหม่", callback_data="regretry"),
        ]
    ]
    await update.message.reply_text(
        f"ชื่อ: {name}\nถูกต้องไหม?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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

    registration.register(user_id, name)
    await edit_text_safe(query, f"ลงทะเบียนสำเร็จ ✅\nชื่อ: {name}")


async def handle_reg_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    pending_registrations.pop(update.effective_user.id, None)
    await edit_text_safe(query, "กรุณาพิมพ์ชื่อ-นามสกุลของคุณใหม่อีกครั้ง")


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
    application.add_handler(CallbackQueryHandler(handle_reg_confirm, pattern="^regconfirm$"))
    application.add_handler(CallbackQueryHandler(handle_reg_retry, pattern="^regretry$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting courier-bot (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
