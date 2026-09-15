"""
SlipOK bank-slip verification — migrated from the standalone
slip-checker sub-project (~/program/openbox/slip-checker/slip_checker.py),
now called directly from the payment flow instead of run by hand.
"""

import requests

from . import config


def check_slip(image_path: str, expected_amount: float | None = None) -> tuple[int, dict]:
    """POST the slip image to SlipOK. Returns (http_status_code, json_body).

    Passing expected_amount lets SlipOK itself flag an amount mismatch;
    the caller should still re-check slip_amount() against the
    reservation's amount_due_thb rather than trust this alone.
    """
    with open(image_path, "rb") as f:
        response = requests.post(
            config.SLIPOK_API_URL,
            headers={"x-authorization": config.SLIPOK_API_KEY},
            files={"files": f},
            data={"amount": str(expected_amount)} if expected_amount else {},
            timeout=30,
        )
    try:
        body = response.json()
    except ValueError:
        body = {"error": "non-JSON response", "raw": response.text}
    return response.status_code, body


def is_success(body: dict) -> bool:
    return bool(body.get("success"))


def slip_amount(body: dict) -> float | None:
    data = body.get("data") or {}
    amount = data.get("amount")
    try:
        return float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return None


def slip_trans_ref(body: dict) -> str | None:
    data = body.get("data") or {}
    return data.get("transRef")
