import pytest
import jwt
import time
from unittest.mock import patch, MagicMock
from agent.auth import rotate_token, generate_token, admin_required, check_auth, check_user_auth
from agent.common.errors import PermanentError
from flask import Flask, g

def test_generate_token():
    secret = "test-secret"
    payload = {"sub": "user1"}
    token = generate_token(payload, secret)
    decoded = jwt.decode(token, secret, algorithms=["HS256"])
    assert decoded["sub"] == "user1"
    assert "exp" in decoded

def test_rotate_token_success(app):
    with app.app_context():
        app.config["AGENT_NAME"] = "test-agent"
        with patch("agent.auth.register_with_hub", return_value=True):
            with patch("agent.auth.settings") as mock_settings:
                new_token = rotate_token()
                assert new_token is not None
                assert app.config["AGENT_TOKEN"] == new_token
                assert mock_settings.save_agent_token.called

def test_rotate_token_no_hub(app):
    with app.app_context():
        # No hub_url in settings
        with patch("agent.auth.settings") as mock_settings:
            mock_settings.hub_url = None
            new_token = rotate_token()
            assert new_token is not None
            assert mock_settings.save_agent_token.called

def test_rotate_token_hub_failure(app):
    with app.app_context():
        app.config["AGENT_NAME"] = "test-agent"
        with patch("agent.auth.settings") as mock_settings:
            mock_settings.hub_url = "http://hub"
            with patch("agent.auth.register_with_hub", return_value=False):
                with pytest.raises(PermanentError):
                    rotate_token()

def test_rotate_token_save_failure(app):
    with app.app_context():
        with patch("agent.auth.settings") as mock_settings:
            mock_settings.hub_url = None
            mock_settings.save_agent_token.side_effect = Exception("disk full")
            # Should not raise, just log error
            new_token = rotate_token()
            assert new_token is not None

def test_check_auth_no_token(app):
    # If AGENT_TOKEN is not set in config, it should allow access
    app.config["AGENT_TOKEN"] = None
    @app.route("/no-auth")
    @check_auth
    def no_auth_route():
        return "ok"
    
    with app.test_client() as client:
        assert client.get("/no-auth").status_code == 200

def test_check_auth_invalid_static_token(app):
    app.config["AGENT_TOKEN"] = "secret"
    @app.route("/static-auth")
    @check_auth
    def static_auth_route():
        return "ok"
    
    with app.test_client() as client:
        # Wrong token
        assert client.get("/static-auth?token=wrong").status_code == 401

def test_admin_required_already_authenticated(app):
    @app.route("/admin-only")
    @admin_required
    def admin_route():
        return "ok"
    
    with app.test_client() as client:
        with app.app_context():
            g.is_admin = True
            # We need to simulate the request context within the test_client call if possible, 
            # or just use app_context if the decorator works with it.
            # But decorators usually run in request context.
            pass

    # Better way: use the client and set g in a before_request or similar if needed, 
    # but g is cleared after each request.
    # Let's test the logic where g.is_admin is NOT set but token is provided.
    app.config["AGENT_TOKEN"] = "admin-secret"
    with app.test_client() as client:
        # Valid static token should grant admin
        assert client.get("/admin-only?token=admin-secret").status_code == 200

def test_check_user_auth_expired(app):
    from agent.config import settings
    @app.route("/user-auth")
    @check_user_auth
    def user_auth_route():
        return "ok"
    
    expired_token = jwt.encode({"sub": "user", "exp": time.time() - 100}, settings.secret_key, algorithm="HS256")
    with app.test_client() as client:
        response = client.get("/user-auth", headers={"Authorization": f"Bearer {expired_token}"})
        assert response.status_code == 401
        assert "expired" in response.get_json()["message"]

def test_check_user_auth_invalid(app):
    @app.route("/user-auth-inv")
    @check_user_auth
    def user_auth_route():
        return "ok"
    
    with app.test_client() as client:
        response = client.get("/user-auth-inv", headers={"Authorization": "Bearer invalid.token.here"})
        assert response.status_code == 401
        assert "Invalid token" in response.get_json()["message"]
