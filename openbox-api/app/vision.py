"""
Claude vision extraction — one function, four prompts.

Two backends selected by VISION_BACKEND in .env:
  - "cli": shell out to the local `claude` CLI (no API key needed, reuses
    this machine's Claude subscription — the trick the old
    telegram-bot/registration.py used). Default until an ANTHROPIC_API_KEY
    is set.
  - "api": call the Anthropic API directly (claude-opus-5, structured
    JSON output). Cheaper per call, the intended production backend.

Every prompt below asks for ONLY a JSON object back, matched to a
TypedDict, so both backends return the same shape regardless of which one
ran.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, TypedDict

from . import config

EXTRACTION_TIMEOUT_SECONDS = 90


# ---------------------------------------------------------------- prompts

STUDENT_ID_PROMPT = """\
Look at the image at this exact path: {image_path}

Decide whether it is a photo of a Thai student ID card (บัตรประจำตัวนักเรียน).
If it is, extract:
- the student's full name exactly as printed on the card (ชื่อ-นามสกุล)
- the student ID / code number printed on the card (รหัสประจำตัวนักเรียน)

NOTE: no real sample of the school's ID card format has been provided yet,
so judge generically (does this look like a genuine Thai student ID card
layout) rather than against one specific school's exact template.

Reply with ONLY a single JSON object, no markdown fences, no extra text:
{{"is_student_id": true or false, "name": "<full name>" or null, \
"student_code": "<id number>" or null, "reason": "<short reason in Thai \
if false or a field could not be read, else null>"}}
"""

EMPLOYEE_CARD_PROMPT = """\
Look at the image at this exact path: {image_path}

Decide whether it looks like a genuine employee / courier ID card for a
delivery or logistics company (e.g. Shopee Xpress, Flash Express, Kerry
Express, J&T Express, Lalamove, Grab, ไปรษณีย์ไทย, or a similar courier
company). This is a plausibility check only — you cannot verify a card is
not forged, so judge on: does it look like a real printed company ID
card (logo, photo, name field, employee ID field), not a screenshot, not
random object, not obviously edited.

If it looks plausible, extract:
- the company name as printed/shown (best guess if logo-only)
- the person's full name as printed on the card
- the employee ID / code printed on the card, if any
- whether the card has a photo of a person on it (has_photo)

Reply with ONLY a single JSON object, no markdown fences, no extra text:
{{"is_employee_card": true or false, "confidence": <0.0-1.0>, \
"company": "<company name>" or null, "name": "<full name>" or null, \
"employee_id": "<id code>" or null, "has_photo": true or false, \
"red_flags": ["<short reason in Thai>", ...], \
"reason": "<short overall reason in Thai if is_employee_card is false, else null>"}}
"""

ORDER_SCREENSHOT_PROMPT = """\
Look at the image at this exact path: {image_path}

It should be a screenshot from a shopping app (Shopee, Lazada, TikTok
Shop, or similar) showing an order the user placed — proof of purchase,
NOT a bank transfer slip. Extract what you can read:
- platform: which app this screenshot is from (e.g. "Shopee", "Lazada",
  "TikTok Shop"), or your best guess from the UI style
- order_id: the order number / order ID shown, if visible
- shop: the seller/shop name, if visible
- recipient_name: the delivery recipient's name, if visible
- tracking_no: a tracking/parcel number, if visible (often not present
  on an order-confirmation screenshot, that's fine — use null)

Reply with ONLY a single JSON object, no markdown fences, no extra text:
{{"is_order_screenshot": true or false, "platform": "<platform>" or null, \
"order_id": "<order id>" or null, "shop": "<shop name>" or null, \
"recipient_name": "<name>" or null, "tracking_no": "<tracking number>" or null, \
"reason": "<short reason in Thai if is_order_screenshot is false, else null>"}}
"""

PARCEL_LABEL_PROMPT = """\
Look at the image at this exact path: {image_path}

It should be a photo of a shipping/parcel label (the sticker on a
package), photographed close-up against a scanner window. Extract
whatever fields you can read from the label:
- recipient_name: the "to" / ผู้รับ name on the label
- phone_last4: the last 4 digits of the recipient's phone number, if a
  phone number is printed on the label
- tracking_no: the tracking/parcel number (often a barcode number
  printed as text too)
- carrier: the courier company name shown on the label, if visible

The photo may be angled, partially cropped, or have glare — extract
whatever is legible and use null for anything you can't read confidently.

Reply with ONLY a single JSON object, no markdown fences, no extra text:
{{"is_parcel_label": true or false, "recipient_name": "<name>" or null, \
"phone_last4": "<4 digits>" or null, "tracking_no": "<tracking number>" or null, \
"carrier": "<carrier name>" or null, \
"reason": "<short reason in Thai if is_parcel_label is false, else null>"}}
"""


class VisionResult(TypedDict, total=False):
    pass  # shape varies per prompt — callers read the keys they asked for


async def extract_json(image_path: str, prompt_template: str) -> dict:
    """Run the given prompt against image_path, return the parsed JSON dict.

    Raises RuntimeError on backend failure, timeout, or an unparseable
    reply — callers should catch this and ask the user to retry.
    """
    prompt = prompt_template.format(image_path=image_path)
    if config.VISION_BACKEND == "api":
        return await _extract_via_api(image_path, prompt)
    return await _extract_via_cli(image_path, prompt)


# --------------------------------------------------------------- cli backend

async def _extract_via_cli(image_path: str, prompt: str) -> dict:
    image_dir = str(Path(image_path).parent)

    proc = await asyncio.create_subprocess_exec(
        config.CLAUDE_BIN,
        "-p",
        prompt,
        "--allowedTools",
        "Read",
        "--add-dir",
        image_dir,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--safe-mode",
        "--strict-mcp-config",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=EXTRACTION_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("claude CLI timed out")

    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {stderr.decode(errors='replace')}")

    try:
        envelope = json.loads(stdout.decode())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude CLI did not return valid JSON envelope: {e}")

    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {envelope.get('result')}")

    return _parse_result_text(envelope.get("result", ""))


# --------------------------------------------------------------- api backend

async def _extract_via_api(image_path: str, prompt: str) -> dict:
    import base64

    import anthropic

    media_type = "image/jpeg"
    suffix = Path(image_path).suffix.lower()
    if suffix == ".png":
        media_type = "image/png"
    elif suffix == ".webp":
        media_type = "image/webp"

    image_b64 = base64.standard_b64encode(Path(image_path).read_bytes()).decode()

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_result_text(text)


# --------------------------------------------------------------- shared parse

def _parse_result_text(raw_result: str) -> dict:
    text = raw_result.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # The model is told to reply with ONLY JSON, but sometimes prepends a
    # sentence of commentary first anyway — fall back to grabbing the
    # outermost {...} object out of the reply and parsing just that.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"could not parse extraction JSON: {e}\nraw: {raw_result!r}")

    raise RuntimeError(f"could not parse extraction JSON: no JSON object found\nraw: {raw_result!r}")
