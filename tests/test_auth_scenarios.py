import pytest
import jwt
import time
from flask import Flask, g
from agent.auth import check_auth, check_user_auth, admin_required
from agent.config import settings

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["AGENT_TOKEN"] = "test-agent-token"
    app.config["TESTING"] = True
    
    # Sicherstellen, dass secret_key für Tests gesetzt ist
    if not settings.secret_key:
        settings.secret_key = "test-secret-key"
    
    # Test routes
    @app.route("/secure")
    @check_auth
    def secure():
        return {"status": "ok", "is_admin": g.get("is_admin", False)}

    @app.route("/user-only")
    @check_user_auth
    def user_only():
        return {"status": "ok", "user": g.get("user")}

    @app.route("/admin-only")
    @admin_required
    def admin_only():
        return {"status": "ok", "is_admin": g.get("is_admin", False)}

    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_agent_token_header(client):
    # Test static agent token in header
    headers = {"Authorization": "Bearer test-agent-token"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True

def test_agent_token_query(client):
    # Test static agent token in query param
    response = client.get("/secure?token=test-agent-token")
    assert response.status_code == 200
    assert response.json["is_admin"] is True

def test_agent_jwt_header(client, app):
    # Test JWT signed with AGENT_TOKEN
    token = jwt.encode({"sub": "hub", "exp": time.time() + 3600}, "test-agent-token", algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True

def test_user_jwt_admin(client):
    # Test User JWT with admin role
    payload = {"username": "admin_user", "role": "admin", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Works for @check_auth
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True
    
    # Works for @admin_required
    response = client.get("/admin-only", headers=headers)
    assert response.status_code == 200

def test_user_jwt_regular(client):
    # Test User JWT with user role
    payload = {"username": "regular_user", "role": "user", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Works for @check_auth
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is False
    
    # Works for @check_user_auth
    response = client.get("/user-only", headers=headers)
    assert response.status_code == 200
    
    # Fails for @admin_required
    response = client.get("/admin-only", headers=headers)
    assert response.status_code == 403

def test_invalid_token(client):
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 401

def test_missing_token(client):
    response = client.get("/secure")
    assert response.status_code == 401
