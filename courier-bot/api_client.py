"""Thin async client for openbox-api (~/program/openbox/openbox-api)."""

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


async def get_rider(tg_user_id: int) -> dict | None:
    resp = await _client.get(f"/riders/{tg_user_id}")
    if resp.status_code == 404:
        return None
    _raise_for_status(resp)
    return resp.json()


async def register_rider(tg_user_id: int, name: str, phone: str | None = None) -> dict:
    data = {"tg_user_id": tg_user_id, "name": name}
    if phone:
        data["phone"] = phone
    resp = await _client.post("/riders/register", data=data)
    _raise_for_status(resp)
    return resp.json()


async def submit_card(tg_user_id: int, image_path: str) -> dict:
    with open(image_path, "rb") as f:
        resp = await _client.post(f"/riders/{tg_user_id}/card", files={"file": ("card.jpg", f, "image/jpeg")})
    _raise_for_status(resp)
    return resp.json()


async def start_drop_session(tg_user_id: int) -> dict:
    resp = await _client.post("/drop-sessions", data={"tg_user_id": tg_user_id})
    _raise_for_status(resp)
    return resp.json()


async def get_drop_session(session_id: int) -> dict:
    resp = await _client.get(f"/drop-sessions/{session_id}")
    _raise_for_status(resp)
    return resp.json()


async def pick_candidate(session_id: int, reservation_id: int) -> dict:
    resp = await _client.post(f"/drop-sessions/{session_id}/pick", data={"reservation_id": reservation_id})
    _raise_for_status(resp)
    return resp.json()


async def confirm_drop(session_id: int) -> dict:
    resp = await _client.post(f"/drop-sessions/{session_id}/confirm")
    _raise_for_status(resp)
    return resp.json()


async def get_reservation(reservation_id: int) -> dict:
    resp = await _client.get(f"/reservations/{reservation_id}")
    _raise_for_status(resp)
    return resp.json()
