#!/usr/bin/env python3
"""
telegram-bot demo (smart locker mockup)

This is a throwaway UI/flow demo, NOT production code. It does not verify
payment or decode any QR content — it just shows the intended chat flow:

    1. User sends a photo to the bot.
    2. Bot replies "⏳ Processing..." and shows the "typing..." chat action.
    3. Bot waits (PROCESSING_DELAY_SECONDS, stand-in for real processing).
    4. Bot edits that message into an inline keyboard (one button per box
       size) to pick a locker box.
    5. On button press, bot edits the message to "sending to shelf..." then,
       after a short beat, "data updated, waiting to receive item" (also
       just messages, no real hardware/API call).

No token is hardcoded. It is read from ../.env (see ../.env.example for the
expected keys) - shared with the other openbox sub-projects.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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


async def edit_text_safe(editable, text: str) -> None:
    """Edit a Message or CallbackQuery's text, ignoring "not modified"."""
    edit = editable.edit_text if hasattr(editable, "edit_text") else editable.edit_message_text
    try:
        await edit(text)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if update.message is None:
        return
    await update.message.reply_text("ส่งรูปภาพมาเพื่อเริ่มต้น (demo: send a photo to start)")


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit(
            "ERROR: TELEGRAM_BOT_TOKEN is not set.\n\n"
            f"Copy {ENV_FILE.parent / '.env.example'} to {ENV_FILE} and "
            "fill in TELEGRAM_BOT_TOKEN, or set it in the environment.\n"
        )

    application = Application.builder().token(token).build()

    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_box_choice))
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
