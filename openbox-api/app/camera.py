"""
Camera control — runs standalone, in-process (no separate kiosk service;
per cj: "ไม่มี server แยก... รันใน rpi เดี่ยวๆเลย" — one app owns the
API, the DB, and the cameras).

cam1 (overhead, CCTV-style): continuous background thread doing simple
frame-diff motion detection. Feeds is_present() (used to gate the pickup
flow so a box can't be opened remotely with nobody in front of it) and
logs presence events.

cam2 (inside the scanner unit): idle until armed for a drop session.
When armed, watches for a frame that differs sharply from the empty
baseline and then holds still for a few frames in a row (a label pressed
flat against the glass), lights the LED, grabs the sharpest of those
frames, and returns it for vision extraction.

Dev note: cj's laptop has one physical webcam (HP True Vision,
/dev/video0 exposed as index 0). CAM2_INDEX defaults to -1 (disabled) in
config.py so the two loops don't fight over the same device — flip
CAM2_INDEX in .env to dev-test the scanner loop instead. On the RPi both
indices point at two real, separate cameras and both run at once.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path

import cv2

from . import config

logger = logging.getLogger("openbox.camera")

PRESENCE_WINDOW_SECONDS = 3.0


class ScannerTimeout(Exception):
    pass


class Camera:
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name
        self.enabled = index >= 0
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()

    def _ensure_open(self) -> bool:
        if self._cap is not None and self._cap.isOpened():
            return True
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            logger.warning("%s: could not open camera index %s", self.name, self.index)
            return False
        return True

    def read_frame(self):
        if not self.enabled:
            return None
        with self._lock:
            if not self._ensure_open():
                return None
            ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None


cam1 = Camera(config.CAM1_INDEX, "cam1-overhead")
cam2 = Camera(config.CAM2_INDEX, "cam2-scanner")

_cam1_thread: threading.Thread | None = None
_cam1_stop = threading.Event()
_presence_lock = threading.Lock()
_last_motion_ts = 0.0


def _mean_abs_diff(a, b) -> float:
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    if ga.shape != gb.shape:
        return 0.0
    return float(cv2.absdiff(ga, gb).mean())


# ------------------------------------------------------------- cam1: motion

def start_cam1_motion_loop() -> None:
    global _cam1_thread
    if not cam1.enabled or not config.CAM1_MOTION_ENABLED:
        logger.info(
            "cam1 motion loop not started (index=%s, CAM1_MOTION_ENABLED=%s)",
            cam1.index, config.CAM1_MOTION_ENABLED,
        )
        return
    _cam1_stop.clear()
    _cam1_thread = threading.Thread(target=_cam1_loop, daemon=True, name="cam1-motion")
    _cam1_thread.start()
    logger.info("cam1 motion loop started on index %s", cam1.index)


def stop_cam1_motion_loop() -> None:
    _cam1_stop.set()
    cam1.release()


def _cam1_loop() -> None:
    global _last_motion_ts
    prev = cam1.read_frame()
    while not _cam1_stop.is_set():
        time.sleep(0.2)
        frame = cam1.read_frame()
        if frame is None or prev is None:
            prev = frame
            continue
        diff = _mean_abs_diff(prev, frame)
        prev = frame
        if diff > config.CAM1_MOTION_THRESHOLD:
            with _presence_lock:
                already_present = (time.time() - _last_motion_ts) < PRESENCE_WINDOW_SECONDS
                _last_motion_ts = time.time()
            if not already_present:
                from . import db
                db.log_event("cam1_motion_start")
                logger.info("cam1: motion detected (diff=%.1f)", diff)


def is_present() -> bool:
    """True if cam1 has seen motion in the last few seconds."""
    if not cam1.enabled:
        # No overhead camera configured (e.g. mid-dev) — don't block the
        # flow on a check we can't perform.
        return True
    with _presence_lock:
        return (time.time() - _last_motion_ts) < PRESENCE_WINDOW_SECONDS


# ------------------------------------------------------------- LED (scanner)

_HAS_GPIO = False
try:
    import RPi.GPIO as GPIO  # type: ignore

    _HAS_GPIO = True
except ImportError:
    GPIO = None  # dev machine has no GPIO — logged simulation instead


def _gpio_setup() -> None:
    if not _HAS_GPIO:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config.SCANNER_LED_PIN, GPIO.OUT)


def led_on() -> None:
    if _HAS_GPIO:
        _gpio_setup()
        GPIO.output(config.SCANNER_LED_PIN, GPIO.HIGH)
    else:
        logger.info("[simulated] scanner LED ON")


def led_off() -> None:
    if _HAS_GPIO:
        GPIO.output(config.SCANNER_LED_PIN, GPIO.LOW)
    else:
        logger.info("[simulated] scanner LED OFF")


def led_blink_twice() -> None:
    for _ in range(2):
        led_on()
        time.sleep(0.15)
        led_off()
        time.sleep(0.15)


# ------------------------------------------------------------- cam2: scanner

def _capture_label_blocking(save_path: Path, timeout_seconds: int) -> Path:
    if not cam2.enabled:
        raise RuntimeError(
            "cam2 (scanner) is not configured — set CAM2_INDEX in .env "
            "(dev machine: temporarily point it at the same index as cam1, "
            "or plug in a second camera; on the RPi it's a separate camera)"
        )

    baseline = cam2.read_frame()
    if baseline is None:
        raise RuntimeError("could not read a frame from cam2")

    led_on()
    try:
        stable_count = 0
        best_frame = None
        prev_frame = baseline
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            time.sleep(0.15)
            frame = cam2.read_frame()
            if frame is None:
                continue
            diff_from_baseline = _mean_abs_diff(baseline, frame)
            diff_from_prev = _mean_abs_diff(prev_frame, frame)
            prev_frame = frame

            # "something new is there" AND "it's holding still" (not a
            # hand still moving past) = a label pressed flat to the glass
            if diff_from_baseline > config.CAM2_CHANGE_THRESHOLD and diff_from_prev < (
                config.CAM2_CHANGE_THRESHOLD / 3
            ):
                stable_count += 1
                best_frame = frame
                if stable_count >= config.CAM2_STABLE_FRAMES:
                    break
            else:
                stable_count = 0

        if best_frame is None:
            raise ScannerTimeout("no parcel label detected within timeout")

        led_blink_twice()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(save_path), best_frame)
        return save_path
    finally:
        led_off()


async def capture_label_photo(save_path: Path, timeout_seconds: int = 60) -> Path:
    """Arm the scanner and wait for a label to be pressed against it.

    Runs the blocking OpenCV loop in a thread executor so it doesn't
    block the FastAPI event loop. Raises ScannerTimeout if nothing shows
    up in time, or RuntimeError if cam2 isn't configured/available.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _capture_label_blocking, save_path, timeout_seconds)
