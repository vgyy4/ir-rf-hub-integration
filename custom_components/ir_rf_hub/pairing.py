"""Pairing code decoding, split out from config_flow.py so it has zero
Home Assistant imports and can be unit-tested without installing HA core.
Mirrors the App's security.py encode_pairing_code -- kept as an
independent reimplementation (not a cross-import) since the App and this
integration are deliberately separate deployables.
"""

from __future__ import annotations

import base64
import json


class PairingCodeError(ValueError):
    pass


def decode_pairing_code(code: str) -> tuple[str, int, str]:
    """Returns (host, port, token)."""
    padding = "=" * (-len(code) % 4)
    try:
        raw = base64.urlsafe_b64decode(code.strip() + padding)
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise PairingCodeError("Invalid pairing code") from exc

    if payload.get("v") != 1 or not all(k in payload for k in ("h", "p", "t")):
        raise PairingCodeError("Invalid pairing code")

    return payload["h"], int(payload["p"]), payload["t"]
