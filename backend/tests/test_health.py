from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_service_is_ready() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "stock-trading-backend",
        "api_version": "v1",
    }

