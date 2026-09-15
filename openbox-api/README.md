# openbox-api

The single standalone backend for the openbox smart locker. Owns the
DB (SQLite), the HTTP API both Telegram bots call, the Claude vision
calls, SlipOK payment verification, the cameras, and (later) the STM32
serial link — **one process, no separate "central server" + "kiosk
client" split**. It runs on cj's dev laptop today (using the laptop's
built-in webcam as cam1); the same code moves onto the RPi unchanged
later — only the `.env` values change (camera indices, serial port).

Full system design: `~/claude/projects/openbox/LOG.txt` (2026-09-15
entry) has the original write-up (rider ID check, 2 cameras, box flow).

## What's built vs. not (this round)

Built: customer registration + order screenshot verification + box
reservation + SlipOK payment + pickup flow; rider registration + phone
verification + employee-card vision check; the drop-off matching flow
(scanner capture -> vision -> match to a reservation -> box assignment);
cam1 (overhead) motion/presence detection against a real webcam; a
serial-link module with the STM32 protocol client, auto-simulated since
no firmware exists yet; Telegram push notifications straight from the
api on drop-complete / pickup-complete.

**Explicitly NOT built this round** (per cj): the admin approval bot/UI
(there's a stopgap manual endpoint, see below) and the STM32 firmware
itself (Arduino/STM32 code — `serial_link.py` simulates every hardware
response until real firmware exists and `STM32_SERIAL_PORT` is set).

cam2 (the scanner camera) is disabled by default (`CAM2_INDEX=-1`) since
the dev laptop only has one physical webcam — the code is written and
wired in, just untested against real hardware until a second camera is
available.

## Run

```bash
cd ~/program/openbox/openbox-api
venv/bin/python -m app.main
# or: venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 4002
```

Health check: `curl http://127.0.0.1:4002/health`

Reads the shared `~/program/openbox/.env` — see `../.env.example` for
every key this app uses (vision backend, camera indices, STM32 port, box
rent, allowed rider companies, etc).

## Setup from scratch

```bash
cd ~/program/openbox/openbox-api
python3 -m venv venv
venv/bin/pip install fastapi "uvicorn[standard]" python-multipart aiosqlite anthropic requests opencv-python-headless
```

(`aiosqlite` and `anthropic` are installed for forward-compat / the "api"
vision backend; the current code path uses plain `sqlite3` and shells out
to the `claude` CLI by default — see `app/vision.py`.)

## Layout

- `app/config.py` — reads `../.env`, all tuning knobs in one place
- `app/db.py` — SQLite schema + connection (customers, riders, boxes,
  reservations, payments, drop_sessions, media, events)
- `app/vision.py` — Claude vision extraction, 4 prompts (student ID,
  employee card, order screenshot, parcel label), 2 backends (`cli` /
  `api`)
- `app/slipok.py` — bank-slip verification (migrated from the old
  standalone `../slip-checker`)
- `app/camera.py` — cam1 motion/presence loop + cam2 scanner capture
  (OpenCV, real webcam), LED control (GPIO if present, else logged
  simulation)
- `app/serial_link.py` — STM32 UART protocol client, auto-simulated
- `app/telegram_push.py` — direct push to a customer/rider's Telegram
  chat (uses the bots' own tokens from `../.env`)
- `app/matching.py` — fuzzy name matching (stdlib `difflib`)
- `app/routers/` — `customers.py`, `riders.py`, `boxes.py`,
  `reservations.py`, `drop.py`

## Rider approval (stopgap — no admin bot yet)

```bash
curl -X POST http://127.0.0.1:4002/riders/<tg_user_id>/approve
curl -X POST http://127.0.0.1:4002/riders/<tg_user_id>/reject -F reason="..."
```

## API surface

See `/docs` (FastAPI's auto-generated Swagger UI) once the app is
running, or `/openapi.json` for the raw schema.
