"""API-level tests exercising the HTTP surface end to end."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_meetings(client):
    response = client.get("/api/meetings")
    assert response.status_code == 200
    assert len(response.json()) == 25


def test_filter_by_source(client):
    crm = client.get("/api/meetings", params={"source": "crm"}).json()
    assert all("crm" in m["sources"] for m in crm)
    assert len(crm) == 20


def test_invalid_source_is_rejected(client):
    assert client.get("/api/meetings", params={"source": "bogus"}).status_code == 400


def test_conflicts_only_filter(client):
    meetings = client.get("/api/meetings", params={"conflicts_only": True}).json()
    assert len(meetings) == 17
    for meeting in meetings:
        assert any(field["conflict"] for field in meeting["fields"].values())


def test_date_range_filter(client):
    meetings = client.get(
        "/api/meetings", params={"date_from": "2025-03-01", "date_to": "2025-03-10"}
    ).json()
    assert len(meetings) == 1
    assert meetings[0]["id"] == "meeting-CRM-1001-CAL-A1"


def test_get_single_meeting(client):
    response = client.get("/api/meetings/meeting-CRM-1001-CAL-A1")
    assert response.status_code == 200
    body = response.json()
    assert body["source_ids"] == {"crm": "CRM-1001", "calendar": "CAL-A1"}


def test_unknown_meeting_returns_404(client):
    assert client.get("/api/meetings/does-not-exist").status_code == 404


def test_conflicts_endpoint(client):
    conflicts = client.get("/api/conflicts").json()
    assert len(conflicts) == 30
    assert {"meeting_id", "field", "chosen_value", "candidates"} <= set(conflicts[0])


def test_stats_endpoint(client):
    stats = client.get("/api/stats").json()
    assert stats["total_meetings"] == 25
    assert stats["matched"] == 17
    assert stats["possible_duplicates"] == 2


def test_reingest_is_idempotent(client):
    before = client.get("/api/stats").json()
    assert client.post("/api/reingest").status_code == 200
    after = client.get("/api/stats").json()
    assert before == after


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Event Sync Service" in response.text
