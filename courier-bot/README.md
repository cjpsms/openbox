# rider-bot (openbox smart locker — courier/delivery side)

Telegram bot for the rider/courier half of the openbox smart locker.
Like [`../telegram-bot`](../telegram-bot), this is a **thin client**:
all state, the Claude vision employee-card check, and the drop-off
matching logic live in [`openbox-api`](../openbox-api/README.md).

## Flow

1. `/start` — if already registered, welcomes back and points at `/drop`.
2. **Register:** type your name -> confirm -> share your phone number
   (Telegram's native contact-share button, so it's a real, verified
   number) -> send a photo of your employee/courier ID card.
3. **Card check:** the api's Claude vision call pre-screens the card
   (looks like a real courier-company ID? name matches what you typed?).
   If it passes, your account goes to `PENDING_APPROVAL`.
4. **Approval — STOPGAP.** There's no admin bot yet (see
   `~/claude/projects/openbox/LOG.txt`), so cj approves riders manually:
   `curl -X POST http://127.0.0.1:4002/riders/<tg_user_id>/approve`
   (or `/reject` with `-F reason=...`). Once approved, openbox-api pushes
   you a Telegram message directly.
5. **Drop off:** once `APPROVED`, send `/drop`. The api arms the scanner
   camera; press the parcel's shipping label against it. The bot polls
   until Claude vision (via the api) has read the label and matched it to
   a customer's reservation — confirm, and the box opens for you to put
   the item in.

Note: the scanner camera (cam2) is disabled by default on the dev
machine (only one physical webcam — see `../openbox-api/README.md`), so
`/drop` will currently end in an "เกิดข้อผิดพลาด" (ERROR) status until a
second camera is configured. Everything up to that point (registration,
card check, matching logic, box assignment) works end-to-end already.

## Setup

A venv already exists at `venv/` with `python-telegram-bot` (v22) and
`httpx` installed. To set up from scratch:

```bash
cd ~/program/openbox/courier-bot
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot httpx
```

**openbox-api must be running** — see
[`../openbox-api/README.md`](../openbox-api/README.md). If it runs
somewhere other than `127.0.0.1:4002`, set `OPENBOX_API_BASE`.

## Bot token

Secrets for every openbox sub-project live in one shared, git-ignored file
at the repo root: `~/program/openbox/.env` (see `../.env.example`).
This bot reads `COURIER_BOT_TOKEN` from there (or the environment, which
takes precedence). If no token is found, `bot.py` exits immediately with
a clear error.

## Run

```bash
cd ~/program/openbox/courier-bot
source venv/bin/activate
python bot.py
```
