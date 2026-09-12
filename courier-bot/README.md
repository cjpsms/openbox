# courier-bot (smart locker demo, delivery/courier side)

This is a **mockup/demo**, not production code, mirroring [../telegram-bot](../telegram-bot)'s
style but for the courier who drops off a package rather than the
customer picking one up.

## Flow (current)

1. Courier sends `/start`.
2. If already registered (by Telegram user id), bot replies with a welcome
   back message using their stored name.
3. If not, bot asks them to type their name. Courier types it, bot shows
   it back with a ✅ ยืนยัน / 🔁 พิมพ์ใหม่ confirm keyboard.
4. On confirm, the account is saved to `courier_users.json` (git-ignored -
   real names, not demo data).

That's it for now - deliberately minimal ("เริ่มให้แบบ สมัคด้วยชื่อ หรืออะไร
ระบบค่อยทำ"). The actual drop-off flow (which box, notifying the
customer, load-cell/weight confirmation, etc.) isn't built yet; registered
couriers just get a placeholder reply to anything else they send.

## Setup

```bash
cd ~/program/openbox/courier-bot
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot
```

## Bot token

Secrets for every openbox sub-project live in one shared, git-ignored file
at the repo root: `~/program/openbox/.env` (see `../.env.example` for the
expected keys). This bot reads `COURIER_BOT_TOKEN` from there (or the
environment, which takes precedence). If no token is found, `bot.py` exits
immediately with a clear error message.

## Run

```bash
cd ~/program/openbox/courier-bot
source venv/bin/activate
python bot.py
```
