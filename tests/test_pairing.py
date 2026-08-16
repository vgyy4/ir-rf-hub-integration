"""Tests custom_components/ir_rf_hub/pairing.py: deliberately has zero
Home Assistant imports (see that module's docstring), so this file needs
nothing beyond the standard library plus pytest.
"""

from __future__ import annotations

import base64
import json

import pytest

from pairing import PairingCodeError, decode_pairing_code


def _encode(host: str, port: int, token: str, *, version: int = 1) -> str:
    """Test-local mirror of the App's encode_pairing_code (security.py) --
    not cross-imported, since the App and this integration are separate
    deployables. Kept in sync by testing the exact wire format both sides
    agree on: base64url(JSON({h, p, t, v})).
    """
    payload = {"h": host, "p": port, "t": token, "v": version}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_round_trip():
    code = _encode("local-ir-rf-hub", 8099, "secret-token-123")
    host, port, token = decode_pairing_code(code)
    assert (host, port, token) == ("local-ir-rf-hub", 8099, "secret-token-123")


def test_round_trip_survives_missing_base64_padding():
    # base64url without '=' padding is exactly what the App emits
    # (rstrip("=")): confirm the decoder's padding math is correct for
    # every possible input length, not just one lucky case.
    for token_len in range(1, 8):
        code = _encode("host", 1, "t" * token_len)
        assert "=" not in code
        _, _, token = decode_pairing_code(code)
        assert token == "t" * token_len


def test_rejects_garbage_input():
    with pytest.raises(PairingCodeError):
        decode_pairing_code("not-valid-base64-json!!!")


def test_rejects_valid_base64_that_isnt_json():
    garbage = base64.urlsafe_b64encode(b"just some bytes").decode("ascii").rstrip("=")
    with pytest.raises(PairingCodeError):
        decode_pairing_code(garbage)


def test_rejects_wrong_version():
    code = _encode("host", 1, "token", version=2)
    with pytest.raises(PairingCodeError):
        decode_pairing_code(code)


def test_rejects_missing_fields():
    raw = json.dumps({"h": "host", "v": 1}).encode("utf-8")  # missing p, t
    code = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    with pytest.raises(PairingCodeError):
        decode_pairing_code(code)


def test_tolerates_surrounding_whitespace():
    code = _encode("host", 6053, "token")
    host, port, token = decode_pairing_code(f"  {code}\n")
    assert (host, port, token) == ("host", 6053, "token")
