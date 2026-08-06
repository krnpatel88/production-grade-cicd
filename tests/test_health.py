"""Tests for GET /health."""


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body_shape(client):
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok Kiran"
    assert "app_name" in body
    assert "app_env" in body
    assert "app_version" in body
