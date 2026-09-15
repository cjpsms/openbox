"""
openbox-api — the single standalone backend for the openbox smart locker.

Owns the DB, the HTTP API for customer-bot/rider-bot, the cameras, and
(later) the STM32 serial link — all in one process, per cj: "ไม่มี server
แยก... รันใน rpi เดี่ยวๆเลย". Runs on the dev laptop today (built-in
webcam as cam1), moves onto the RPi unchanged later (just different .env
values for camera indices / serial port).

Run: venv/bin/uvicorn app.main:app --host <config.HOST> --port <config.PORT>
(or just `venv/bin/python -m app.main`, see __main__ below)
"""

import logging

from fastapi import FastAPI

from . import camera, config, db, serial_link
from .routers import boxes, customers, drop, reservations, riders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("openbox.main")

app = FastAPI(title="openbox-api")

app.include_router(customers.router)
app.include_router(riders.router)
app.include_router(boxes.router)
app.include_router(reservations.router)
app.include_router(drop.router)


@app.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    camera.start_cam1_motion_loop()
    logger.info(
        "openbox-api up — vision backend=%s, cam1=%s, cam2=%s, stm32 simulated=%s",
        config.VISION_BACKEND, camera.cam1.enabled, camera.cam2.enabled, serial_link.is_simulated(),
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    camera.stop_cam1_motion_loop()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "vision_backend": config.VISION_BACKEND,
        "cam1_enabled": camera.cam1.enabled,
        "cam2_enabled": camera.cam2.enabled,
        "stm32_simulated": serial_link.is_simulated(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False)
