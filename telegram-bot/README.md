# telegram-bot (smart locker demo)

This is a **mockup/demo**, not production code. It exists to show one possible
flow for a future "smart locker" idea — it does not decode QR codes or verify
any real payment.

## Flow

0. **Registration (first time only).** User sends a photo of their student
   ID card. `registration.py` extracts name + student code (see below) and
   asks the user to confirm before saving the account to
   `registered_users.json` (git-ignored — real names/IDs, not a demo file).
1. Once registered, sending a photo starts the order flow: bot replies
   "⏳ Processing..." with the "typing..." chat action.
2. Bot waits (stand-in for real processing).
3. Bot edits that message into an inline keyboard to pick a locker box.
4. On button press, the bot edits the message to confirm, e.g.
   "เลือกกล่อง 1 แล้ว ✅ เปิดล็อกเกอร์..."

### Student ID extraction

`registration.py` shells out to the local `claude` CLI (Claude Code) with
`--allowedTools Read` pointed at the downloaded photo, asking it to decide
whether the image is a Thai student ID card and, if so, extract the name
and student code as JSON. This reuses the machine's own Claude
subscription instead of needing a separate `ANTHROPIC_API_KEY` — same
approach as the home-server hermes-agent bridge. Each call costs
real subscription usage (~$0.15-0.20/call as measured, mostly fixed
tool-schema overhead, not image size) since every call is a fresh `claude`
process with no session reuse — fine for occasional registrations, not
built for high volume.

**Not yet using the school's real ID card format** (no sample photo
received as of 2026-09-12) — the extraction prompt is generic ("does this
look like a Thai student ID card"). Once a real sample is available,
tighten `STUDENT_ID_PROMPT` in `registration.py` to check the school's
actual layout/logo/field labels so it rejects non-matching cards instead
of accepting anything that looks vaguely like a student ID.

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
