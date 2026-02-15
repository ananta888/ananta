import pytest
from agent.common.audit import _sanitize_details


def test_sanitize_simple_password():
    """Test dass einfache Passwort-Felder maskiert werden"""
    details = {"password": "secret123", "username": "admin"}
    result = _sanitize_details(details)
    assert result["password"] == "***REDACTED***"
    assert result["username"] == "admin"


def test_sanitize_nested_dict():
    """Test dass verschachtelte Dicts korrekt sanitisiert werden"""
    details = {"user": {"username": "admin", "password": "secret", "api_key": "abc123"}, "action": "login"}
    result = _sanitize_details(details)
    assert result["user"]["password"] == "***REDACTED***"
    assert result["user"]["api_key"] == "***REDACTED***"
    assert result["user"]["username"] == "admin"
    assert result["action"] == "login"


def test_sanitize_list_of_dicts():
    """Test dass Listen mit Dicts korrekt sanitisiert werden"""
    details = {"users": [{"username": "user1", "password": "pass1"}, {"username": "user2", "token": "token123"}]}
    result = _sanitize_details(details)
    assert result["users"][0]["password"] == "***REDACTED***"
    assert result["users"][1]["token"] == "***REDACTED***"
    assert result["users"][0]["username"] == "user1"


def test_sanitize_string_with_embedded_secrets():
    """Test dass Strings mit eingebetteten Secrets maskiert werden"""
    details = {
        "command": "curl -H 'Authorization: Bearer abc123' http://api.com",
        "log": "User password=secret123 logged in",
    }
    result = _sanitize_details(details)
    assert "***" in result["command"]
    assert "***" in result["log"]
    assert "secret123" not in result["log"]


def test_sanitize_all_sensitive_fields():
    """Test dass alle definierten sensitiven Felder maskiert werden"""
    details = {
        "password": "pass",
        "new_password": "newpass",
        "old_password": "oldpass",
        "api_key": "key123",
        "token": "tok456",
        "secret": "sec789",
        "authorization": "Bearer abc",
    }
    result = _sanitize_details(details)
    for key in details.keys():
        assert result[key] == "***REDACTED***", f"Field {key} was not sanitized"


def test_sanitize_case_insensitive():
    """Test dass Sanitization case-insensitive ist"""
    details = {"PASSWORD": "secret", "Api_Key": "key123", "TOKEN": "tok456"}
    result = _sanitize_details(details)
    # Die Keys bleiben erhalten, aber Values werden maskiert
    assert result["PASSWORD"] == "***REDACTED***"
    assert result["Api_Key"] == "***REDACTED***"
    assert result["TOKEN"] == "***REDACTED***"


def test_sanitize_non_dict():
    """Test dass Non-Dict Input unverändert zurückgegeben wird"""
    assert _sanitize_details("string") == "string"
    assert _sanitize_details(123) == 123
    assert _sanitize_details(None) is None


def test_sanitize_empty_dict():
    """Test dass leeres Dict korrekt gehandhabt wird"""
    result = _sanitize_details({})
    assert result == {}
