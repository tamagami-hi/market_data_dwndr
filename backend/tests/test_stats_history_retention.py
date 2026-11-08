"""Telemetry history has no retention limit, and the full history is pageable.

Every recorded session, sweep, reconciliation and escalation is kept forever, so the
stores must stay correct and quick with thousands of rows, and ``/api/stats`` (polled
every few seconds) must keep reading only the recent 30.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stats as stats_api
from app.api.stats import collect_stats, create_stats_router
from app.ops import incidents, stats_store

START = date(2015, 1, 1)


def _day(index: int) -> str:
    return (START + timedelta(days=index)).isoformat()


def _summary(index: int) -> dict:
    return {
        "trading_date": _day(index),
        "captures": 23_000 + index % 100,
        "captured_seconds": 23_000 + index % 100,
        "scheduled_seconds": 23_100,
        "scheduled_seconds_elapsed": 23_100,
        "missing_seconds": 100 - index % 100,
        "stale_feed_seconds": 100 - index % 100,
        "streams": [
            {"underlying": name, "frames_written": 23_000, "frame_loss_pct": 0.4,
             "file_bytes": 450_000_000}
            for name in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX",
                         "STOCKS", "INDICES_FnO")
        ],
    }


def _seed_history(tmp_path, count: int) -> None:
    """Write ``count`` rows directly, in the format ``record_session_summary`` produces."""
    lines = [json.dumps({**_summary(i), "generations": 1}) for i in range(count)]
    stats_store.session_history_path(tmp_path).write_text("\n".join(lines) + "\n")


# --- no write-time pruning ------------------------------------------------------------


def test_the_500th_session_does_not_evict_the_first(tmp_path):
    for index in range(500):
        stats_store.record_session_summary(tmp_path, _summary(index))

    rows = stats_store.load_session_history(tmp_path)
    assert len(rows) == 500
    assert rows[0]["trading_date"] == _day(0)
    page = stats_store.session_history_page(tmp_path, limit=1, before=_day(1))
    assert [r["trading_date"] for r in page["sessions"]] == [_day(0)]
    assert page["total"] == 500


def test_reconciliation_history_is_never_pruned(tmp_path):
    for index in range(400):
        stats_store.record_reconciliation(
            tmp_path, {"trading_date": _day(index), "data_loss_pct": 0.1}
        )
    rows = stats_store.load_reconciliation_history(tmp_path)
    assert len(rows) == 400
    assert rows[0]["trading_date"] == _day(0)
    assert stats_store.reconciliation_for_dates(tmp_path, {_day(0)})[_day(0)]["data_loss_pct"]


def test_escalation_ledger_keeps_every_timestamp(tmp_path):
    for index in range(75):
        stats_store.record_escalation(tmp_path, "2026-11-05", 1_000 + index)
    ledger = stats_store.load_escalations(tmp_path, "2026-11-05")
    assert ledger["count"] == 75
    assert len(ledger["timestamps"]) == 75
    assert ledger["timestamps"][0] == 1_000


def test_rerecording_a_date_preserves_other_rows_byte_for_byte(tmp_path):
    path = stats_store.session_history_path(tmp_path)
    path.write_text(
        '{"trading_date": "2026-11-03", "captures": 7,   "odd_spacing": true}\n'
        "this line is not JSON\n"
        '{"trading_date": "2026-11-04", "captures": 1}\n'
    )
    stats_store.record_session_summary(tmp_path, {"trading_date": "2026-11-04", "captures": 2})

    lines = path.read_text().splitlines()
    assert lines[0] == '{"trading_date": "2026-11-03", "captures": 7,   "odd_spacing": true}'
    assert lines[1] == "this line is not JSON", "an undecodable line is never dropped"
    assert json.loads(lines[2])["captures"] == 2
    assert json.loads(lines[2])["generations"] == 2


def test_incident_ledger_keeps_every_day(tmp_path):
    for index in range(400):
        incidents.record_incident(tmp_path, _day(index), incidents.KIND_PROCESS_START, index)
    assert len(list(tmp_path.glob("incidents-*.json"))) == 400


# --- scale ----------------------------------------------------------------------------


def test_three_thousand_sessions_stay_fast(tmp_path):
    _seed_history(tmp_path, 3_000)

    started = time.perf_counter()
    stats_store.record_session_summary(tmp_path, _summary(3_000))
    write_s = time.perf_counter() - started

    started = time.perf_counter()
    recent = stats_store.session_history(tmp_path, 30)
    page = stats_store.session_history_page(tmp_path, limit=500, before=_day(1_000))
    read_s = time.perf_counter() - started

    assert [r["trading_date"] for r in recent][:2] == [_day(3_000), _day(2_999)]
    assert page["total"] == 3_001
    assert page["sessions"][0]["trading_date"] == _day(999)
    assert len(page["sessions"]) == 500
    assert page["next_before"] == _day(500)
    assert write_s < 2.0, f"recording one session into 3,000 took {write_s:.2f}s"
    assert read_s < 1.0, f"reading pages out of 3,000 sessions took {read_s:.2f}s"


# --- API ------------------------------------------------------------------------------


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(
        stats_api,
        "get_settings",
        lambda: SimpleNamespace(stats_dir=tmp_path, expected_frames_per_session=23_100),
    )
    app = FastAPI()
    app.include_router(create_stats_router())
    return TestClient(app)


def test_the_paginated_endpoint_walks_the_full_history(monkeypatch, tmp_path):
    _seed_history(tmp_path, 53)
    client = _client(monkeypatch, tmp_path)

    seen: list[str] = []
    before = None
    pages = 0
    while True:
        params = {"limit": 7, **({"before": before} if before else {})}
        body = client.get("/api/stats/sessions", params=params).json()
        assert body["total"] == 53
        seen.extend(row["trading_date"] for row in body["sessions"])
        assert all("outcome" in row and "loss_causes" in row for row in body["sessions"])
        pages += 1
        before = body["next_before"]
        if before is None:
            break

    assert pages == 8
    assert seen == [_day(i) for i in reversed(range(53))]


def test_the_paginated_endpoint_defaults_to_fifty(monkeypatch, tmp_path):
    _seed_history(tmp_path, 60)
    body = _client(monkeypatch, tmp_path).get("/api/stats/sessions").json()
    assert len(body["sessions"]) == 50
    assert body["next_before"] == _day(10)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"limit": "ten"},
        {"before": "yesterday"},
        {"before": "2026-13-01"},
        {"before": "2026-02-30"},
    ],
)
def test_the_paginated_endpoint_validates_its_parameters(monkeypatch, tmp_path, params):
    response = _client(monkeypatch, tmp_path).get("/api/stats/sessions", params=params)
    assert response.status_code == 422


def test_dashboard_stats_still_carry_only_the_recent_thirty(monkeypatch, tmp_path):
    _seed_history(tmp_path, 40)
    monkeypatch.setattr(
        stats_api, "get_settings", lambda: SimpleNamespace(stats_dir=tmp_path)
    )
    payload = collect_stats(SimpleNamespace())
    assert len(payload["session_history"]) == 30
    assert payload["session_history"][0]["trading_date"] == _day(39)
    assert payload["session_history"][0]["outcome"] == "complete"


def test_the_incident_endpoint_returns_a_days_ledger(monkeypatch, tmp_path):
    incidents.record_incident(tmp_path, "2026-11-05", incidents.KIND_PROCESS_START, 1)
    client = _client(monkeypatch, tmp_path)

    body = client.get("/api/stats/incidents/2026-11-05").json()
    assert body["recorded"] is True
    assert body["entries"][0]["kind"] == "process_start"
    assert body["analysis"]["process_starts"] == 1
    assert client.get("/api/stats/incidents/2026-11-31").status_code == 422
    assert client.get("/api/stats/incidents/..%2Fsecrets").status_code in (404, 422)
