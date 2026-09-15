"""
Fire-and-forget push messages to a customer/rider's Telegram chat, so
the api can notify someone the moment something happens server-side
(item stored, box ready for pickup) instead of the bots having to poll.

Uses the same bot tokens the bots themselves use (shared ../.env) — a
Telegram chat_id equals the user's tg_user_id for a private chat, so no
extra mapping table is needed.
"""

import logging
import os

import requests

logger = logging.getLogger("openbox.telegram_push")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(token: str | None, tg_user_id: int, text: str) -> None:
    if not token:
        logger.warning("no bot token configured — cannot push to %s: %s", tg_user_id, text)
        return
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": tg_user_id, "text": text},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("telegram push to %s failed: %s %s", tg_user_id, resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.warning("telegram push to %s failed: %s", tg_user_id, e)


def push_to_customer(tg_user_id: int, text: str) -> None:
    _send(os.environ.get("TELEGRAM_BOT_TOKEN"), tg_user_id, text)


def push_to_rider(tg_user_id: int, text: str) -> None:
    _send(os.environ.get("COURIER_BOT_TOKEN"), tg_user_id, text)
