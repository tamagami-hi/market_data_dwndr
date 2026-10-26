"""Tests for the token API's TOTP codes (RFC 6238, HMAC-SHA1, six digits)."""

from __future__ import annotations

import base64

import pytest

from app.kite.totp import TotpSeedError, code_for_step, decode_seed, step_at

# RFC 6238 appendix B's SHA-1 seed.
RFC_SEED = b"12345678901234567890"


@pytest.mark.parametrize(
    ("unix_seconds", "code"),
    [(59, "287082"), (1_111_111_109, "081804"), (1_234_567_890, "005924")],
)
def test_codes_match_the_rfc_6238_vectors(unix_seconds, code):
    assert code_for_step(RFC_SEED, step_at(unix_seconds)) == code


def test_seed_decodes_without_padding_spaces_or_case():
    text = base64.b32encode(RFC_SEED).decode().rstrip("=")
    spaced = " ".join(text[i : i + 4] for i in range(0, len(text), 4)).lower()

    assert decode_seed(spaced) == RFC_SEED


@pytest.mark.parametrize("seed", ["not base32!", "", base64.b32encode(b"short").decode()])
def test_unusable_seeds_are_refused(seed):
    with pytest.raises(TotpSeedError):
        decode_seed(seed)
