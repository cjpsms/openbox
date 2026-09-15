"""
Central config for openbox-api.

Reads the shared ~/program/openbox/.env (same file every openbox
sub-project reads — see ~/claude/projects/openbox/LOG.txt for why it's
one shared file instead of per-project .env files).

This app is designed to run as ONE standalone process — no separate
"central server" + "kiosk client" split. It owns the DB, the HTTP API for
the bots, the cameras, and (later) the STM32 serial link, all in one
process. Today it runs on cj's dev machine using the laptop's built-in
webcam; the same code is meant to move onto the RPi as-is later — only
the .env values (camera indices, serial port) change.
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent  # openbox-api/
OPENBOX_ROOT = PROJECT_ROOT.parent  # ~/program/openbox/
ENV_FILE = OPENBOX_ROOT / ".env"

MEDIA_DIR = PROJECT_ROOT / "media"
DB_PATH = PROJECT_ROOT / "openbox.db"


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Telegram / SlipOK (shared with the bots) ---
SLIPOK_API_KEY = os.environ.get("SLIPOK_API_KEY", "")
SLIPOK_API_URL = os.environ.get("SLIPOK_API_URL", "")

# --- Claude vision ---
# "cli"  -> shell out to the local `claude` CLI (no ANTHROPIC_API_KEY needed,
#           reuses this machine's Claude subscription — same trick the old
#           telegram-bot/registration.py used). Fine for dev / low volume.
# "api"  -> call the Anthropic API directly with ANTHROPIC_API_KEY. Cheaper
#           per-call and the intended production backend once a key exists
#           (see design LOG: CLI trick is too expensive per call for a
#           real service). Falls back to "cli" if no key is set.
VISION_BACKEND = os.environ.get("VISION_BACKEND", "cli").strip().lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if VISION_BACKEND == "api" and not ANTHROPIC_API_KEY:
    VISION_BACKEND = "cli"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-opus-5")

# --- Cameras ---
# -1 disables that camera. On cj's dev laptop there is one physical webcam
# (HP True Vision, /dev/video0) so CAM2 defaults OFF to avoid both loops
# fighting over the same device — flip CAM2_INDEX in .env to test the
# scanner loop standalone. On the RPi both indices point at real, separate
# cameras (cam1 overhead, cam2 inside the scanner).
CAM1_INDEX = _env_int("CAM1_INDEX", 0)
CAM2_INDEX = _env_int("CAM2_INDEX", -1)
CAM1_MOTION_ENABLED = os.environ.get("CAM1_MOTION_ENABLED", "1") == "1"
SCANNER_LED_PIN = _env_int("SCANNER_LED_PIN", 18)  # BCM numbering, RPi only

# Motion / scanner tuning (see camera.py for how these are used)
CAM1_MOTION_THRESHOLD = _env_float("CAM1_MOTION_THRESHOLD", 25.0)  # mean abs pixel diff
CAM1_MOTION_MIN_AREA_FRAC = _env_float("CAM1_MOTION_MIN_AREA_FRAC", 0.02)  # frac of frame
CAM2_STABLE_FRAMES = _env_int("CAM2_STABLE_FRAMES", 6)  # frames image must stay ~still
CAM2_CHANGE_THRESHOLD = _env_float("CAM2_CHANGE_THRESHOLD", 18.0)  # diff vs empty baseline

# --- STM32 serial link (protocol only — no firmware yet, see LOG) ---
STM32_SERIAL_PORT = os.environ.get("STM32_SERIAL_PORT", "")  # empty = simulate
STM32_BAUD = _env_int("STM32_BAUD", 115200)

# --- Business rules ---
DROP_SESSION_TTL_SECONDS = _env_int("DROP_SESSION_TTL_SECONDS", 300)
BOX_RENT_THB = {
    "small": _env_float("BOX_RENT_SMALL_THB", 20.0),
    "large": _env_float("BOX_RENT_LARGE_THB", 35.0),
}

RIDER_ALLOWED_COMPANIES = [
    c.strip()
    for c in os.environ.get(
        "RIDER_ALLOWED_COMPANIES",
        "Shopee Xpress,Flash Express,Kerry Express,J&T Express,Lalamove,Grab,ไปรษณีย์ไทย",
    ).split(",")
    if c.strip()
]

HOST = os.environ.get("OPENBOX_API_HOST", "127.0.0.1")
PORT = _env_int("OPENBOX_API_PORT", 4002)
