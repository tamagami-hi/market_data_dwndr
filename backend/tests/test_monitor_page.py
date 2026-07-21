"""Test that the Capture Monitor page is served."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_monitor_page_served():
    client = TestClient(app)
    resp = client.get("/monitor")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Capture Monitor" in body
    assert "/ws/capture-status" in body  # dashboard wires to the capture-status topic


def test_health_still_ok():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
