#!/usr/bin/env python3
"""
Student ID registration (openbox smart-locker demo)

Before using the locker flow, a user must register by sending a photo of
their student ID card. We shell out to the local `claude` CLI (Claude Code)
to read the image and extract the student's name + student code — this
reuses the machine's own Claude subscription (same trick as the
home-server hermes-agent bridge) instead of needing a separate
ANTHROPIC_API_KEY.

NOTE: no real sample of the school's ID card format has been provided yet
(as of 2026-09-12), so STUDENT_ID_PROMPT below is generic ("does this look
like a Thai student ID card, extract name + code"). Once a real sample
photo is available, tighten this prompt to check for the school's actual
layout/logo/field labels so it rejects cards that don't match.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, TypedDict

REGISTRY_FILE = Path(__file__).resolve().parent / "registered_users.json"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

STUDENT_ID_PROMPT = """\
Use the Read tool to look at the image at this exact path: {image_path}

Decide whether it is a photo of a Thai student ID card (บัตรประจำตัวนักเรียน).
If it is, extract:
- the student's full name exactly as printed on the card (ชื่อ-นามสกุล)
- the student ID / code number printed on the card (รหัสประจำตัวนักเรียน)

Reply with ONLY a single JSON object, no markdown code fences, no extra
text before or after it, in exactly this shape:
{{"is_student_id": true or false, "name": "<full name>" or null, \
"student_code": "<id number>" or null, "reason": "<short reason in Thai \
if is_student_id is false or a field could not be read, else null>"}}
"""

EXTRACTION_TIMEOUT_SECONDS = 90


class ExtractionResult(TypedDict):
    is_student_id: bool
    name: Optional[str]
    student_code: Optional[str]
    reason: Optional[str]


async def extract_student_id(image_path: str) -> ExtractionResult:
    """Run `claude -p` against the image and parse its JSON verdict.

    Raises RuntimeError on a CLI failure, timeout, or unparseable reply —
    callers should catch this and ask the user to retry rather than crash.
    """
    prompt = STUDENT_ID_PROMPT.format(image_path=image_path)
    image_dir = str(Path(image_path).parent)

    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN,
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

    raw_result = envelope.get("result", "")
    text = raw_result.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse extraction JSON: {e}\nraw: {raw_result!r}")

    return ExtractionResult(
        is_student_id=bool(parsed.get("is_student_id")),
        name=parsed.get("name"),
        student_code=parsed.get("student_code"),
        reason=parsed.get("reason"),
    )


def _load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {}
    return json.loads(REGISTRY_FILE.read_text())


def _save_registry(registry: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


def is_registered(user_id: int) -> bool:
    return str(user_id) in _load_registry()


def get_registration(user_id: int) -> Optional[dict]:
    return _load_registry().get(str(user_id))


def register(user_id: int, name: str, student_code: str) -> None:
    from datetime import datetime, timezone

    registry = _load_registry()
    registry[str(user_id)] = {
        "name": name,
        "student_code": student_code,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(registry)
