"""
STM32 serial link — protocol client only. The STM32 firmware itself is
explicitly out of scope for this round (cj: build everything except
admin and the Arduino code). When STM32_SERIAL_PORT is unset, or the
port can't be opened, every call here SIMULATES a successful hardware
response (clearly logged as [simulated]) so the drop/pickup flow still
completes end-to-end for testing the bots and API today. Once the real
firmware exists, set STM32_SERIAL_PORT in .env and these same functions
start talking to the real board — no caller changes needed.

Protocol (see ~/claude/projects/openbox/LOG.txt, 2026-09-04 hardware plan):
    RPi -> STM32:  OPEN <n> | STATUS | TARE <n> | PING
    STM32 -> RPi:  OK | EV DOOR <n> OPEN | EV DOOR <n> CLOSED
                   | EV WEIGHT <n> <grams> | EV FAULT <msg> | PONG
"""

import asyncio
import logging

from . import config

logger = logging.getLogger("openbox.stm32")

_serial = None
_simulated = True


def _try_open_serial() -> None:
    global _serial, _simulated
    if not config.STM32_SERIAL_PORT:
        logger.info("STM32_SERIAL_PORT not set — running in SIMULATED mode")
        _simulated = True
        return
    try:
        import serial  # pyserial

        _serial = serial.Serial(config.STM32_SERIAL_PORT, config.STM32_BAUD, timeout=1)
        _simulated = False
        logger.info("STM32 serial link open on %s @ %s baud", config.STM32_SERIAL_PORT, config.STM32_BAUD)
    except Exception as e:
        logger.warning(
            "could not open STM32 serial port %s (%s) — falling back to SIMULATED mode",
            config.STM32_SERIAL_PORT, e,
        )
        _simulated = True


_try_open_serial()


def is_simulated() -> bool:
    return _simulated


def _box_number(box_label: str) -> int:
    """'box-1' -> 1"""
    return int(box_label.rsplit("-", 1)[-1])


async def open_box(box_label: str) -> None:
    n = _box_number(box_label)
    if _simulated:
        logger.info("[simulated] OPEN %s (no STM32 firmware yet)", n)
        await asyncio.sleep(0.3)
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _serial.write, f"OPEN {n}\n".encode())


async def wait_for_door_closed(box_label: str, timeout: float = 60.0) -> bool:
    """True once the limit switch confirms the door is latched shut."""
    if _simulated:
        logger.info("[simulated] waiting for door %s to close — auto-confirming in 2s", box_label)
        await asyncio.sleep(2.0)
        return True
    return await _wait_for_line(f"EV DOOR {_box_number(box_label)} CLOSED", timeout)


async def wait_for_weight_confirm(box_label: str, timeout: float = 15.0) -> bool:
    """True once the HX711 reports a weight event for this box."""
    if _simulated:
        logger.info("[simulated] confirming weight change on %s — auto-confirming", box_label)
        await asyncio.sleep(0.5)
        return True
    return await _wait_for_line(f"EV WEIGHT {_box_number(box_label)}", timeout, prefix=True)


async def _wait_for_line(expected: str, timeout: float, prefix: bool = False) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        line = await loop.run_in_executor(None, _serial.readline)
        text = line.decode(errors="replace").strip()
        if text and ((prefix and text.startswith(expected)) or text == expected):
            return True
    return False
