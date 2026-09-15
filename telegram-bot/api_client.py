"""
Thin async client for openbox-api (~/program/openbox/openbox-api).

Replaces the old local registration.py (JSON file + direct `claude` CLI
call) — the bot is now a thin client; openbox-api owns the DB, the
Claude vision calls, and SlipOK verification, so both bots and any
future admin tool see the same state.
"""

import os

import httpx

API_BASE = os.environ.get("OPENBOX_API_BASE", "http://127.0.0.1:4002")

_client = httpx.AsyncClient(base_url=API_BASE, timeout=120.0)


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(resp.status_code, str(detail))


async def get_customer(tg_user_id: int) -> dict | None:
    resp = await _client.get(f"/customers/{tg_user_id}")
    if resp.status_code == 404:
        return None
    _raise_for_status(resp)
    return resp.json()


async def verify_student_id(image_path: str) -> dict:
    with open(image_path, "rb") as f:
        resp = await _client.post("/customers/verify-id", files={"file": ("id.jpg", f, "image/jpeg")})
    _raise_for_status(resp)
    return resp.json()


async def register_customer(tg_user_id: int, name: str, student_code: str) -> dict:
    resp = await _client.post(
        "/customers/register",
        data={"tg_user_id": tg_user_id, "name": name, "student_code": student_code},
    )
    _raise_for_status(resp)
    return resp.json()


async def verify_order_screenshot(image_path: str) -> dict:
    with open(image_path, "rb") as f:
        resp = await _client.post("/customers/orders/verify", files={"file": ("order.jpg", f, "image/jpeg")})
    _raise_for_status(resp)
    return resp.json()


async def create_reservation(tg_user_id: int, box_size: str, order: dict) -> dict:
    resp = await _client.post(
        "/reservations",
        data={
            "tg_user_id": tg_user_id,
            "box_size": box_size,
            "order_platform": order.get("platform"),
            "order_id": order.get("order_id"),
            "recipient_name": order.get("recipient_name"),
            "tracking_no": order.get("tracking_no"),
        },
    )
    _raise_for_status(resp)
    return resp.json()


async def get_pending_reservation(tg_user_id: int) -> dict | None:
    """The customer's most recent AWAITING_PAYMENT reservation, if any."""
    resp = await _client.get("/reservations", params={"tg_user_id": tg_user_id, "status": "AWAITING_PAYMENT"})
    _raise_for_status(resp)
    rows = resp.json()
    return rows[0] if rows else None


async def get_stored_reservation(tg_user_id: int) -> dict | None:
    """The customer's reservation that's ready for pickup, if any."""
    resp = await _client.get("/reservations", params={"tg_user_id": tg_user_id, "status": "STORED"})
    _raise_for_status(resp)
    rows = resp.json()
    return rows[0] if rows else None


async def pay_reservation(reservation_id: int, image_path: str) -> dict:
    with open(image_path, "rb") as f:
        resp = await _client.post(
            f"/reservations/{reservation_id}/pay", files={"file": ("slip.jpg", f, "image/jpeg")}
        )
    _raise_for_status(resp)
    return resp.json()


async def request_pickup(reservation_id: int, tg_user_id: int) -> dict:
    resp = await _client.post(f"/reservations/{reservation_id}/pickup-request", data={"tg_user_id": tg_user_id})
    _raise_for_status(resp)
    return resp.json()
