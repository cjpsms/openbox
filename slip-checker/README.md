# slip-checker (openbox payment verification)

Checks a photographed Thai bank-transfer slip against the [SlipOK](https://slipok.com)
API — a step toward verifying a real payment before releasing an openbox
locker, rather than trusting the QR image alone (see the open item in
[telegram-bot](../telegram-bot)).

## What it does

Uploads a slip image to SlipOK. If the image contains a valid slip QR code,
SlipOK returns the transaction's sender/receiver names, bank, amount,
timestamp, and transaction ref (`transRef`). If the image has no QR code
at all (e.g. an unrelated photo), SlipOK returns an error instead
(`code: 1007`, "รูปภาพไม่มี QR Code").

Note: this only confirms the slip's QR decodes to a real transaction record
via SlipOK — it does not by itself detect a slip being reused twice. For
that, the caller would need to track `transRef` values already seen.

## Setup

```bash
sudo pacman -S python-requests
```

## API key

No key is hardcoded. Secrets for every openbox sub-project live in one
shared, git-ignored file at the repo root: `~/program/openbox/.env` (see
`../.env.example` for the expected keys — `SLIPOK_API_KEY`, `SLIPOK_API_URL`).
Copy `../.env.example` to `../.env` and fill in the real values.

## Run

```bash
cd ~/program/openbox/slip-checker
python3 slip_checker.py /path/to/slip.jpg
python3 slip_checker.py /path/to/slip.jpg 400   # also assert the amount
```
