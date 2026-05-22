"""Tests for the web backend API."""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from web.backend.main import app
from web.backend import database as db


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "test.db"))
    db.init_db()


@pytest.fixture(autouse=True)
def mock_graph_runner():
    """Mock GraphRunner.run so it completes instantly without calling the real graph."""
    async def fake_run(self):
        db.update_analysis_status(self.analysis_id, "complete", signal="BUY", confidence=75.0)
        await self.queue.put({
            "type": "analysis_complete", "agent": "system",
            "content": "BUY", "stats": {"tokens": 0}, "timestamp": "2025-01-01T00:00:00Z",
        })

    with patch("web.backend.graph_runner.GraphRunner.run", fake_run):
        yield


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestSettings:
    def test_get_settings(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "llm_provider" in data
        assert "deep_think_llm" in data

    def test_update_settings(self, client):
        r = client.put("/api/settings", json={"max_debate_rounds": 3})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r = client.get("/api/settings")
        assert r.json()["max_debate_rounds"] == 3


class TestAnalyze:
    def test_start_analysis(self, client):
        r = client.post("/api/analyze", json={
            "ticker": "AAPL",
            "trade_date": "2025-01-15",
        })
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert data["status"] == "pending"

    def test_get_status_not_found(self, client):
        r = client.get("/api/analyze/nonexistent/status")
        assert r.status_code == 404

    def test_get_status(self, client):
        r = client.post("/api/analyze", json={
            "ticker": "NVDA",
            "trade_date": "2025-01-15",
        })
        aid = r.json()["id"]

        r = client.get(f"/api/analyze/{aid}/status")
        assert r.status_code == 200
        assert r.json()["status"] in ("pending", "running", "complete")


class TestHistory:
    def test_empty_history(self, client):
        r = client.get("/api/history")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_history_with_data(self, client):
        client.post("/api/analyze", json={"ticker": "TSLA", "trade_date": "2025-01-10"})
        client.post("/api/analyze", json={"ticker": "AAPL", "trade_date": "2025-01-11"})

        r = client.get("/api/history")
        assert r.json()["total"] == 2

    def test_history_filter_ticker(self, client):
        client.post("/api/analyze", json={"ticker": "TSLA", "trade_date": "2025-01-10"})
        client.post("/api/analyze", json={"ticker": "AAPL", "trade_date": "2025-01-11"})

        r = client.get("/api/history", params={"ticker": "TSLA"})
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["ticker"] == "TSLA"


class TestReports:
    def test_report_not_found(self, client):
        r = client.get("/api/reports/nonexistent")
        assert r.status_code == 404

    def test_report_detail(self, client):
        r = client.post("/api/analyze", json={"ticker": "GOOG", "trade_date": "2025-01-12"})
        aid = r.json()["id"]

        r = client.get(f"/api/reports/{aid}")
        assert r.status_code == 200
        assert r.json()["analysis"]["ticker"] == "GOOG"

    def test_delete_report(self, client):
        r = client.post("/api/analyze", json={"ticker": "META", "trade_date": "2025-01-13"})
        aid = r.json()["id"]

        r = client.delete(f"/api/reports/{aid}")
        assert r.status_code == 200

        r = client.get(f"/api/reports/{aid}")
        assert r.status_code == 404

    def test_export_md(self, client):
        r = client.post("/api/analyze", json={"ticker": "AMZN", "trade_date": "2025-01-14"})
        aid = r.json()["id"]

        r = client.get(f"/api/reports/{aid}/export", params={"format": "md"})
        assert r.status_code == 200
        assert "AMZN" in r.json()["content"]


class TestDashboard:
    def test_dashboard_empty(self, client):
        r = client.get("/api/dashboard")
        assert r.status_code == 200
        assert r.json()["recent"] == []

    def test_dashboard_with_data(self, client):
        client.post("/api/analyze", json={"ticker": "AAPL", "trade_date": "2025-01-15"})
        r = client.get("/api/dashboard")
        assert len(r.json()["recent"]) == 1


class TestWebSocket:
    def test_ws_completed_analysis(self, client):
        """WS on a completed analysis with no stored events should close cleanly."""
        # Create an analysis that completes via mock
        r = client.post("/api/analyze", json={"ticker": "WS", "trade_date": "2025-01-01"})
        aid = r.json()["id"]

        import time
        time.sleep(0.5)  # let background task complete

        with client.websocket_connect(f"/ws/analyze/{aid}") as ws:
            # Should receive stored events (the analysis_complete from mock)
            data = ws.receive_json()
            assert data["type"] == "analysis_complete"
