def test_register_user(client):
    response = client.post("/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "secret123",
        "role": "author",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"
    assert data["role"] == "author"
    assert "id" in data


def test_register_duplicate(client):
    client.post("/auth/register", json={
        "username": "dupuser",
        "email": "dup@example.com",
        "password": "secret123",
    })
    response = client.post("/auth/register", json={
        "username": "dupuser",
        "email": "dup@example.com",
        "password": "secret123",
    })
    assert response.status_code == 409


def test_login_success(client):
    client.post("/auth/register", json={
        "username": "loginuser",
        "email": "login@example.com",
        "password": "correctpw",
    })
    response = client.post("/auth/login", json={
        "username": "loginuser",
        "password": "correctpw",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client):
    client.post("/auth/register", json={
        "username": "authuser",
        "email": "auth@example.com",
        "password": "correctpw",
    })
    response = client.post("/auth/login", json={
        "username": "authuser",
        "password": "wrongpw",
    })
    assert response.status_code == 401
