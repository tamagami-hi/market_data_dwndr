"""Feed health: three independent signals, one classification.

"Is the feed OK?" was previously a single boolean derived from a content digest, which
collapsed three genuinely different conditions into one alarm:

* **transport** — are broker packets arriving at all? (a half-open socket, a dead uplink)
* **artifact** — is a particular logical dataset receiving *relevant* updates? The socket
  can be perfectly healthy while index derivatives stop updating.
* **content**  — are the values actually changing? Unchanged values may simply mean a
  quiet market, which is not a fault at all.

Conflating them has a concrete cost: a single frozen artifact looked identical to a dead
uplink, and a quiet market looked like both. Restart-first recovery is the right response
to a dead *transport*; it is the wrong response to one stale artifact and an absurd
response to a quiet market.

Market phase and feed health are deliberately **separate** dimensions (§23): a session can
be PRE_OPEN while the feed is HEALTHY, or OPEN while the transport is stale. Nothing here
knows about sessions; the caller supplies ``capture_expected`` and gets ``INACTIVE`` when
no data is owed.
"""

from __future__ import annotations

from collections.abc import Mapping

# Nothing is expected right now (outside the session, or the session is disabled), so feed
# health is not applicable rather than "bad".
HEALTH_INACTIVE = "INACTIVE"
# Packets arriving and the expected artifacts are updating.
HEALTH_HEALTHY = "HEALTHY"
# Packets arriving, artifacts being routed to, but the values are not materially changing.
HEALTH_QUIET = "QUIET"
# The socket is alive and delivering, but one or more expected artifacts are not receiving
# relevant updates. Recorded and exposed; NOT on its own a reason to restart the process.
HEALTH_ARTIFACT_STALE = "ARTIFACT_STALE"
# Broker packets themselves are not arriving. This is what restart-first recovery is for.
HEALTH_TRANSPORT_STALE = "TRANSPORT_STALE"
# A failure has outlived the stale deadline and a restart escalation is imminent.
HEALTH_RECOVERY_PENDING = "RECOVERY_PENDING"
# The day's restart budget is spent; capture is up but knowingly not receiving data.
HEALTH_RECOVERY_ABANDONED = "RECOVERY_ABANDONED"

# Ordered worst-first. Used for reporting precedence, not for arithmetic.
HEALTH_PRECEDENCE = (
    HEALTH_RECOVERY_ABANDONED,
    HEALTH_RECOVERY_PENDING,
    HEALTH_TRANSPORT_STALE,
    HEALTH_ARTIFACT_STALE,
    HEALTH_QUIET,
    HEALTH_HEALTHY,
    HEALTH_INACTIVE,
)

# A dataset is considered stale once it has gone this many multiples of the content-stale
# threshold without a relevant update. Artifacts are intentionally more tolerant than the
# content signal: a single index with no trades for a few seconds is ordinary.
ARTIFACT_STALE_FACTOR = 3.0


def stale_artifacts(
    artifact_ages_ms: Mapping[str, int | None],
    stale_after_ms: int,
    *,
    factor: float = ARTIFACT_STALE_FACTOR,
) -> tuple[str, ...]:
    """Names of artifacts with no relevant update inside the tolerance.

    ``None`` means "never updated", which counts as stale — a dataset that has received
    nothing all session is the most severe artifact-level condition there is, and treating
    it as healthy is exactly how a silently unsubscribed instrument would hide.
    """
    threshold = max(1, int(stale_after_ms * factor))
    return tuple(
        name
        for name, age in sorted(artifact_ages_ms.items())
        if age is None or age >= threshold
    )


def classify(
    *,
    capture_expected: bool,
    transport_age_ms: int | None,
    content_age_ms: int | None,
    stale_after_ms: int,
    artifact_ages_ms: Mapping[str, int | None] | None = None,
    recovery_pending: bool = False,
    recovery_abandoned: bool = False,
) -> str:
    """Classify feed health from the three signals.

    Precedence is worst-first: an abandoned recovery outranks a pending one, which
    outranks a dead transport, which outranks a stale artifact, which outranks a quiet
    market. Only the *reported* condition is single-valued — the underlying signals stay
    separately observable in telemetry.
    """
    if recovery_abandoned:
        return HEALTH_RECOVERY_ABANDONED
    if not capture_expected:
        return HEALTH_INACTIVE
    if recovery_pending:
        return HEALTH_RECOVERY_PENDING

    # Transport first: if packets are not arriving, nothing downstream can be trusted.
    if transport_age_ms is not None and transport_age_ms >= stale_after_ms:
        return HEALTH_TRANSPORT_STALE

    stale_names = stale_artifacts(artifact_ages_ms or {}, stale_after_ms)
    if stale_names:
        return HEALTH_ARTIFACT_STALE

    # Packets arriving and artifacts updating, but values not moving: a quiet market, not
    # a fault. This is the case the old single-signal design could not express.
    if content_age_ms is not None and content_age_ms >= stale_after_ms:
        return HEALTH_QUIET

    return HEALTH_HEALTHY


def is_fault(health: str) -> bool:
    """True for conditions that represent a real problem (not INACTIVE/QUIET/HEALTHY)."""
    return health in (
        HEALTH_ARTIFACT_STALE,
        HEALTH_TRANSPORT_STALE,
        HEALTH_RECOVERY_PENDING,
        HEALTH_RECOVERY_ABANDONED,
    )


__all__ = [
    "ARTIFACT_STALE_FACTOR",
    "HEALTH_ARTIFACT_STALE",
    "HEALTH_HEALTHY",
    "HEALTH_INACTIVE",
    "HEALTH_PRECEDENCE",
    "HEALTH_QUIET",
    "HEALTH_RECOVERY_ABANDONED",
    "HEALTH_RECOVERY_PENDING",
    "HEALTH_TRANSPORT_STALE",
    "classify",
    "is_fault",
    "stale_artifacts",
]
