def _register_and_login(client, username="author", role="author"):
    client.post("/auth/register", json={
        "username": username,
        "email": f"{username}@test.com",
        "password": "pass123",
        "role": role,
    })
    resp = client.post("/auth/login", json={"username": username, "password": "pass123"})
    return resp.json()["access_token"]


def test_create_post(client):
    token = _register_and_login(client)
    response = client.post("/posts/", json={
        "title": "Test Post",
        "content": "This is a test post content.",
        "status": "draft",
    }, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Post"
    assert data["status"] == "draft"


def test_create_post_unauthorized(client):
    response = client.post("/posts/", json={
        "title": "Hack Post",
        "content": "Should not work",
    })
    assert response.status_code == 403


def test_list_posts(client):
    token = _register_and_login(client)
    client.post("/posts/", json={"title": "Post 1", "content": "Content 1"}, headers={"Authorization": f"Bearer {token}"})
    response = client.get("/posts/")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


def test_reader_cannot_create_post(client):
    token = _register_and_login(client, username="reader", role="reader")
    response = client.post("/posts/", json={
        "title": "Reader Post",
        "content": "Should fail",
    }, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
