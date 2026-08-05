"""Tests for GET/POST /users."""


def test_list_users_returns_seed_data(client):
    response = client.get("/users")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 2
    assert {"name", "email", "id"}.issubset(body[0].keys())


def test_create_user_returns_201(client):
    payload = {"name": "Grace Hopper", "email": "grace@example.com"}
    response = client.post("/users", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == payload["name"]
    assert body["email"] == payload["email"]
    assert "id" in body


def test_create_user_persists_in_list(client):
    payload = {"name": "Margaret Hamilton", "email": "margaret@example.com"}
    client.post("/users", json=payload)
    response = client.get("/users")
    emails = [u["email"] for u in response.json()]
    assert payload["email"] in emails


def test_create_user_rejects_invalid_email(client):
    payload = {"name": "Bad Email", "email": "not-an-email"}
    response = client.post("/users", json=payload)
    assert response.status_code == 422


def test_create_user_rejects_empty_name(client):
    payload = {"name": "", "email": "valid@example.com"}
    response = client.post("/users", json=payload)
    assert response.status_code == 422
