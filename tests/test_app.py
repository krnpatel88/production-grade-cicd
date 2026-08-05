"""Tests for app-level behavior: root endpoint and OpenAPI docs."""


def test_root_returns_message(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()


def test_docs_available(client):
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_available(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"]
