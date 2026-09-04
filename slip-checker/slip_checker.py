#!/usr/bin/env python3
"""
slip-checker (openbox smart-locker payment verification)

Checks a Thai bank-transfer slip image (must contain the slip's QR code)
against the SlipOK API and prints the verification result: sender/receiver,
amount, transaction ref, timestamp. A step toward openbox's locker payment
flow (verifying a courier/customer's slip before releasing a box), currently
run standalone against a saved image.

No API key is hardcoded. It is read from ../.env (see ../.env.example for
the expected keys) - shared with the other openbox sub-projects.
"""

import os
import sys
from pathlib import Path

import requests

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file() -> None:
    """Load KEY=VALUE lines from ../.env into os.environ, if it exists."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_env_file()


def check_slip(image_path: str, expected_amount: str | None = None):
    """POST the slip image to SlipOK. Returns (status_code, response_json)."""
    api_key = os.environ.get("SLIPOK_API_KEY")
    api_url = os.environ.get("SLIPOK_API_URL")
    if not api_key or not api_url:
        sys.exit(
            "ERROR: SLIPOK_API_KEY / SLIPOK_API_URL not set.\n\n"
            f"Copy {ENV_FILE.parent / '.env.example'} to {ENV_FILE} and "
            "fill them in, or set them in the environment.\n"
        )

    headers = {"x-authorization": api_key}
    with open(image_path, "rb") as f:
        files = {"files": f}
        data = {"amount": expected_amount} if expected_amount else {}
        response = requests.post(api_url, headers=headers, files=files, data=data)

    return response.status_code, response.json()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python slip_checker.py <image_path> [expected_amount]")

    path = sys.argv[1]
    amount = sys.argv[2] if len(sys.argv) > 2 else None

    status, body = check_slip(path, amount)
    print("Status Code:", status)
    print("Response:", body)
