# telegram-bot (smart locker demo)

This is a **mockup/demo**, not production code. It exists to show one possible
flow for a future "smart locker" idea — it does not decode QR codes or verify
any real payment.

## Flow

1. User sends a photo to the bot.
2. Bot replies "กำลังประมวลผล..." (processing...).
3. Bot waits 5 seconds (stand-in for real processing).
4. Bot sends an inline keyboard (2x2) to pick a locker box: กล่อง 1-4.
5. On button press, the bot edits the message to confirm, e.g.
   "เลือกกล่อง 1 แล้ว ✅ เปิดล็อกเกอร์..."

## Setup

A venv already exists at `venv/` with `python-telegram-bot` (v22, uses the
async v20+ API) installed. To set up from scratch:

```bash
cd ~/program/openbox/telegram-bot
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot
```

## Bot token

The bot needs a real Telegram bot token from [@BotFather](https://t.me/BotFather)
(`/newbot` in a chat with it). No token is hardcoded anywhere in this repo.

Secrets for every openbox sub-project live in one shared, git-ignored file at
the repo root: `~/program/openbox/.env` (see `../.env.example` for the
expected keys). Set it one of two ways:

- `../.env`: copy `../.env.example` to `../.env` and fill in
  `TELEGRAM_BOT_TOKEN`.
- Environment variable: `export TELEGRAM_BOT_TOKEN=your-token-here`
  (takes precedence over `../.env`).

If no token is found, `bot.py` exits immediately with a clear error message
instead of failing obscurely.

## Run

```bash
cd ~/program/openbox/telegram-bot
source venv/bin/activate
python bot.py
```

Then, in Telegram, open a chat with your bot and send it any photo.
