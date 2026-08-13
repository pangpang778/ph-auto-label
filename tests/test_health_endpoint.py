"""Contract tests for the lightweight service health endpoint."""


def test_health_endpoint_returns_ok_without_model_initialization(isolated_app):
    response = isolated_app.test_client().get("/api/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["secret_key"]
