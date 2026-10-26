"""RFC 6238 TOTP codes for the broker login token API.

The broker login service gates its token API with a per-account seed (base32,
HMAC-SHA1, six digits, 30 s steps, the same as an authenticator app). This
module only computes codes; the seed itself never leaves the process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct

STEP_SECONDS = 30
DIGITS = 6
# 80 bits, the shortest seed the broker login service accepts.
MIN_SEED_BYTES = 10


class TotpSeedError(ValueError):
    """Raised when a seed is not usable base32."""


def decode_seed(seed: str) -> bytes:
    """Decode a base32 seed, ignoring case, spaces and missing padding."""
    text = "".join(seed.split()).upper().rstrip("=")
    padded = text + "=" * (-len(text) % 8)
    try:
        raw = base64.b32decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise TotpSeedError("TOTP seed is not valid base32") from exc
    if len(raw) < MIN_SEED_BYTES:
        raise TotpSeedError("TOTP seed is shorter than 80 bits")
    return raw


def step_at(unix_seconds: float) -> int:
    return int(unix_seconds) // STEP_SECONDS


def code_for_step(seed: bytes, step: int) -> str:
    digest = hmac.new(seed, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)
