"""Consolidated read-only status API.

``GET /api/status`` merges session/auth, daily automation, live capture telemetry
(the monitor's per-underlying + global metrics), and the last EOD compression progress
into one payload. It is curl-friendly: default JSON, or ``?format=text`` renders the
monitor dashboard as a plain-text table for the terminal.

No secrets are returned and no state is mutated, so the endpoint is safe to expose on
the same (loopback/Tailscale) interface as the rest of the read-only API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.session import now_ms


def _fmt_bytes(n: int | None) -> str:
    n = int(n or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    if n <= 0:
        return "0 B"
    size = float(n)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.1f} {units[unit]}" if unit else f"{int(size)} B"


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%H:%M:%S")


def collect_status(app_state) -> dict:
    """Gather a JSON-friendly status snapshot from the app state (no secrets)."""
    service = getattr(app_state, "session_service", None)
    automation = getattr(app_state, "daily_automation", None)
    controller = getattr(app_state, "capture_controller", None)

    snapshot: dict = {
        "generated_at": now_ms(),
        "configured": service is not None,
        "session": None,
        "automation": None,
        "capture": {"available": False, "running": False},
        "monitor": None,
        "compression": None,
    }
    if service is not None:
        try:
            snapshot["session"] = service.status()
        except Exception:  # noqa: BLE001 - status must never raise
            snapshot["session"] = {"configured": True, "error": "status unavailable"}
    if automation is not None:
        try:
            auto = automation.status()
            snapshot["automation"] = auto
            snapshot["compression"] = auto.get("compression")
        except Exception:  # noqa: BLE001
            snapshot["automation"] = {"error": "status unavailable"}
    if controller is not None:
        try:
            snapshot["capture"] = controller.status()
            snapshot["monitor"] = controller.monitor_snapshot()
        except Exception:  # noqa: BLE001
            snapshot["capture"] = {"available": True, "error": "status unavailable"}
    return snapshot


def render_text(snapshot: dict) -> str:
    """Render the status snapshot as a terminal dashboard (the monitor, in text)."""
    lines: list[str] = []
    when = _fmt_ms(snapshot.get("generated_at"))
    lines.append(f"market_data_dwndr — status @ {when}")
    lines.append("=" * 60)

    if not snapshot.get("configured"):
        lines.append("BACKEND     not configured (missing .env / settings)")
        return "\n".join(lines) + "\n"

    session = snapshot.get("session") or {}
    auth = "yes" if session.get("authenticated") else "no"
    rate = session.get("risk_free_rate")
    rate_txt = (
        f"{rate} (as_of {session.get('risk_free_rate_as_of') or '-'})"
        if rate is not None
        else "-"
    )
    broker = "configured" if session.get("external_token_source_configured") else "missing"
    lines.append(
        f"SESSION     authenticated={auth}  date={session.get('trading_date') or '-'}  "
        f"phase={session.get('market_phase') or '-'}"
    )
    lines.append(f"            risk_free_rate={rate_txt}  token_broker={broker}")

    automation = snapshot.get("automation") or {}
    if automation:
        lines.append(
            f"AUTOMATION  phase={automation.get('phase') or '-'}  "
            f"last_action={automation.get('last_action') or '-'}  "
            f"error={automation.get('last_error') or '-'}"
        )

    capture = snapshot.get("capture") or {}
    if capture.get("available"):
        indices = ",".join(capture.get("indices") or []) or "-"
        lines.append(
            f"CAPTURE     running={'yes' if capture.get('running') else 'no'}  "
            f"tokens={capture.get('tokens', 0)}  indices={indices}  "
            f"error={capture.get('error') or '-'}"
        )
    else:
        lines.append("CAPTURE     unavailable")

    comp = snapshot.get("compression") or {}
    if comp:
        total = comp.get("bytes_total") or 0
        done = comp.get("bytes_done") or 0
        pct = int(done / total * 100) if total else (100 if comp.get("phase") == "done" else 0)
        lines.append(
            f"COMPRESSION {comp.get('phase', 'idle')}  {pct}%  "
            f"files={comp.get('files_done', 0)}/{comp.get('files_total', 0)}  "
            f"{_fmt_bytes(done)}->{_fmt_bytes(comp.get('zst_bytes'))}  "
            f"ratio={comp.get('ratio', 0)}x  threads={comp.get('threads', '-')}"
        )
    else:
        lines.append("COMPRESSION idle (no end-of-day sweep yet)")

    monitor = snapshot.get("monitor")
    if monitor:
        g = monitor.get("global") or {}
        lines.append(
            f"GLOBAL      fps={g.get('fps', 0)}  captures={g.get('captures', 0)}  "
            f"disk={_fmt_bytes(g.get('disk_bytes'))}  tokens={g.get('tokens', 0)}"
        )
        rows = monitor.get("per_underlying") or []
        if rows:
            lines.append("PER-UNDERLYING")
            lines.append(
                f"  {'name':<10}{'conn':<6}{'hb':<6}{'frames':>10}"
                f"{'file':>12}{'last':>10}{'unmatched':>11}"
            )
            for u in rows:
                lines.append(
                    f"  {str(u.get('underlying', '?')):<10}"
                    f"{('on' if u.get('connected') else 'off'):<6}"
                    f"{('ok' if u.get('heartbeat_ok') else 'stale'):<6}"
                    f"{u.get('frames_written', 0):>10}"
                    f"{_fmt_bytes(u.get('file_bytes')):>12}"
                    f"{_fmt_ms(u.get('last_tick_ms')):>10}"
                    f"{u.get('unmatched', 0):>11}"
                )
    else:
        lines.append("MONITOR     no live telemetry (capture not running)")

    return "\n".join(lines) + "\n"


def create_status_router() -> APIRouter:
    router = APIRouter(tags=["status"])

    @router.get("/api/status")
    async def status(
        request: Request,
        format: str = Query("json", pattern="^(json|text)$"),
    ):
        snapshot = collect_status(request.app.state)
        if format == "text":
            return PlainTextResponse(render_text(snapshot))
        return snapshot

    return router
