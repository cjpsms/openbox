# customer-bot (openbox smart locker — customer side)

Telegram bot for the customer half of the openbox smart locker. It is a
**thin client**: all state (registration, reservations, payments), the
Claude vision calls, and SlipOK verification live in
[`openbox-api`](../openbox-api/README.md), which this bot talks to over
HTTP (`api_client.py`). The bot itself owns nothing but the Telegram chat
UI and short-lived, in-memory "what is this user confirming right now"
state.

## Flow

0. **Registration (first time only).** Send a photo of your student ID
   card. The api's Claude vision call extracts name + student code;
   confirm to register.
1. **Order a locker.** Send a screenshot of your order from a shopping
   app (Shopee/Lazada/etc). The api extracts platform/order id/recipient;
   confirm, then pick a box size. The api creates a reservation and
   quotes the rent due.
2. **Pay.** Send a photo of the bank-transfer slip. The api verifies it
   via SlipOK (amount + transaction ref not reused). On success the
   reservation moves to "waiting for drop-off."
3. **Get notified.** openbox-api pushes a Telegram message to you
   directly (not through this bot process) the moment a rider drops your
   item off.
4. **Pick up.** `/pickup` once notified — shows a button that opens the
   box (the api first checks its overhead camera sees someone standing
   there).

## Setup

A venv already exists at `venv/` with `python-telegram-bot` (v22) and
`httpx` installed. To set up from scratch:

```bash
cd ~/program/openbox/telegram-bot
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot httpx
```

**openbox-api must be running** (`cd ../openbox-api && venv/bin/python -m app.main`)
before this bot can do anything beyond `/start` for an unregistered user —
see [`../openbox-api/README.md`](../openbox-api/README.md). If the api runs
somewhere other than `127.0.0.1:4002`, set `OPENBOX_API_BASE` in the
environment.

## Bot token

Secrets for every openbox sub-project live in one shared, git-ignored file
at the repo root: `~/program/openbox/.env` (see `../.env.example`).
Set `TELEGRAM_BOT_TOKEN` there, or export it in the environment (takes
precedence). If no token is found, `bot.py` exits immediately with a clear
error.

## Run

```bash
cd ~/program/openbox/telegram-bot
source venv/bin/activate
python bot.py
```

Then, in Telegram, open a chat with your bot and send it a photo.
