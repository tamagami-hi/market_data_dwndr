"""Single consolidated health endpoint.

``GET /health`` reports three components in one JSON envelope so a human, an external
monitor, and the Docker healthcheck all read the same source of truth:

* **process** — the event loop is servicing HTTP (implicit: if this handler runs at all).
* **capture_task** — ``ok`` while running, ``idle`` off-hours/pre-open, or ``dead`` when
  the in-process task has crashed and needs a fresh process (``CaptureController.has_failed``).
* **data_freshness** — ``ok`` when ticks are fresh, ``idle`` when capture isn't running,
  ``stale`` when the live feed has no fresh ticks (either the exchange has not started
  trading yet, or the feed has died and the process will restart itself over it — the
  message and ``recovery_armed`` distinguish the two), or ``dead`` once the day's restart
  budget is spent and recovery has been *abandoned*.

Critically, the **HTTP status code encodes liveness only**: ``503`` iff a component is
*dead*, otherwise ``200`` — even when degraded/stale. The Docker healthcheck's
``urllib.request.urlopen`` raises on the 503 (→ non-zero exit → ``unhealthy``), while a
frozen feed stays ``200`` so it never drives a restart loop. Actual crash recovery is
driven by the app self-exiting (see ``capture._default_fatal_handler``); this endpoint is
the observability + ``depends_on: service_healthy`` gate.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.session import now_ms

# Per-component codes carried inside the JSON list (independent of the HTTP status).
CODE_OK = 200
CODE_STALE = 409  # degraded-but-alive: feed frozen, self-healing in progress
CODE_DEAD = 503  # unrecoverable in-process: needs a fresh process


def _market_phase(app_state) -> str | None:
    service = getattr(app_state, "session_service", None)
    if service is None:
        return None
    try:
        return service.status().get("market_phase")
    except Exception:  # noqa: BLE001 - a probe must never raise
        return None


def _capture_check(controller) -> dict:
    if controller is not None and getattr(controller, "has_failed", False):
        return {
            "component": "capture_task",
            "status": "dead",
            "code": CODE_DEAD,
            "message": "capture task crashed; process restart imminent",
        }
    if controller is None or not getattr(controller, "running", False):
        return {
            "component": "capture_task",
            "status": "idle",
            "code": CODE_OK,
            "message": "capture not running (idle / outside market hours)",
        }
    return {
        "component": "capture_task",
        "status": "ok",
        "code": CODE_OK,
        "message": "capture running",
    }


def _freshness_check(controller) -> dict:
    if controller is None or not getattr(controller, "running", False):
        return {
            "component": "data_freshness",
            "status": "idle",
            "code": CODE_OK,
            "message": "no live feed to assess (capture not running)",
        }
    snapshot = None
    try:
        snapshot = controller.monitor_snapshot()
    except Exception:  # noqa: BLE001 - a probe must never raise
        snapshot = None
    if not snapshot:
        return {
            "component": "data_freshness",
            "status": "ok",
            "code": CODE_OK,
            "message": "capture warming up (telemetry not yet available)",
        }
    g = snapshot.get("global") or {}
    data_age_ms = g.get("data_age_ms")
    reconnects = g.get("reconnects")
    recovery = {
        "data_age_ms": data_age_ms,
        "liveness_age_ms": g.get("liveness_age_ms"),
        "frozen_batches": g.get("frozen_batches"),
        "reconnects": reconnects,
        "stale_spell_seconds": g.get("stale_spell_seconds"),
        "longest_stale_spell_seconds": g.get("longest_stale_spell_seconds"),
        "escalations": g.get("escalations"),
        "recovery_abandoned": bool(g.get("recovery_abandoned")),
        "recovery_armed": bool(g.get("recovery_armed")),
    }
    if bool(g.get("exhausted")) or bool(g.get("recovery_abandoned")):
        # Restart-first recovery has spent the day's restart budget without restoring a
        # live feed. A real dead signal: surface 503 so the healthcheck / restart tooling
        # and the operator see it, rather than pretending recovery is still in progress.
        return {
            "component": "data_freshness",
            "status": "dead",
            "code": CODE_DEAD,
            "message": (
                f"live feed stale for {g.get('stale_spell_seconds')}s after "
                f"{g.get('escalations')} restart escalation(s); recovery abandoned for "
                "today — capture is running but not receiving data"
            ),
            **recovery,
        }
    if bool(g.get("degraded")) or bool(g.get("stale")):
        # Two very different situations share the "no fresh ticks" signal: the exchange
        # has not started trading yet (normal, recovery disarmed), or the feed has died
        # mid-session (a fault the process will restart itself over).
        if g.get("recovery_armed"):
            message = (
                f"live feed stale for {g.get('stale_spell_seconds')}s "
                f"({data_age_ms} ms since content changed); process will restart itself "
                "if this persists"
            )
        else:
            message = (
                f"no fresh ticks ({data_age_ms} ms) but the market is not trading yet; "
                "restart-first recovery is disarmed"
            )
        return {
            "component": "data_freshness",
            "status": "stale",
            "code": CODE_STALE,
            "message": message,
            **recovery,
        }
    return {
        "component": "data_freshness",
        "status": "ok",
        "code": CODE_OK,
        "message": "ticks fresh",
        "data_age_ms": data_age_ms,
        "liveness_age_ms": g.get("liveness_age_ms"),
        "frozen_batches": g.get("frozen_batches"),
        "reconnects": reconnects,
    }


def build_health(app_state) -> tuple[dict, int]:
    """Assemble the health envelope and its HTTP status (``200`` unless *dead* → ``503``).

    Pure and never raises, so it is safe both as a probe and to unit-test directly.
    """
    controller = getattr(app_state, "capture_controller", None)

    process_check = {
        "component": "process",
        "status": "ok",
        "code": CODE_OK,
        "message": "event loop responsive",
    }
    checks = [process_check, _capture_check(controller), _freshness_check(controller)]

    if any(c["status"] == "dead" for c in checks):
        overall, http_status = "dead", CODE_DEAD
    elif any(c["status"] == "stale" for c in checks):
        overall, http_status = "degraded", CODE_OK
    else:
        overall, http_status = "ok", CODE_OK

    payload = {
        "status": overall,
        "version": __version__,
        "generated_at": now_ms(),
        "market_phase": _market_phase(app_state),
        "checks": checks,
    }
    return payload, http_status


def create_health_router() -> APIRouter:
    router = APIRouter(tags=["ops"])

    @router.get("/health")
    async def health(request: Request) -> JSONResponse:
        """Single liveness/readiness probe (see module docstring)."""
        payload, http_status = build_health(request.app.state)
        return JSONResponse(payload, status_code=http_status)

    return router
