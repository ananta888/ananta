from agent.common.redaction import redact


def test_redact_simple_password():
    """Test dass einfache Passwort-Felder maskiert werden"""
    details = {"password": "secret123", "username": "admin"}
    result = redact(details)
    assert "***REDACTED_SECRET***" in result["password"]
    assert result["username"] == "admin"


def test_redact_nested_dict():
    """Test dass verschachtelte Dicts korrekt maskiert werden"""
    details = {"user": {"username": "admin", "password": "secret", "api_key": "abc123"}, "action": "login"}
    result = redact(details)
    assert "***REDACTED_SECRET***" in result["user"]["password"]
    assert "***REDACTED_TOKEN***" in result["user"]["api_key"]
    assert result["user"]["username"] == "admin"
    assert result["action"] == "login"


def test_redact_list_of_dicts():
    """Test dass Listen mit Dicts korrekt maskiert werden"""
    details = {"users": [{"username": "user1", "password": "pass1"}, {"username": "user2", "token": "token123"}]}
    result = redact(details)
    assert "***REDACTED_SECRET***" in result["users"][0]["password"]
    assert "***REDACTED_TOKEN***" in result["users"][1]["token"]
    assert result["users"][0]["username"] == "user1"


def test_redact_string_with_embedded_secrets():
    """Test dass Strings mit eingebetteten Secrets maskiert werden"""
    details = {
        "command": "curl -H 'Authorization: Bearer abc123' http://api.com",
        "log": "User password=secret123 logged in",
    }
    result = redact(details)
    assert "***" in result["command"]
    assert "***" in result["log"]
    assert "secret123" not in result["log"]


def test_redact_all_sensitive_fields():
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
    result = redact(details)
    for key in details.keys():
        assert "***REDACTED_" in result[key], f"Field {key} was not redacted"


def test_redact_case_insensitive():
    """Test dass Redaction case-insensitive ist"""
    details = {"PASSWORD": "secret", "Api_Key": "key123", "TOKEN": "tok456"}
    result = redact(details)
    # Die Keys bleiben erhalten, aber Values werden maskiert
    assert "***REDACTED_SECRET***" in result["PASSWORD"]
    assert "***REDACTED_TOKEN***" in result["Api_Key"]
    assert "***REDACTED_TOKEN***" in result["TOKEN"]


def test_redact_non_dict():
    """Test dass Non-Dict Input maskiert zurückgegeben wird (wenn String)"""
    assert redact("password=secret") != "password=secret"
    assert redact(123) == 123
    assert redact(None) is None


def test_redact_empty_dict():
    """Test dass leeres Dict korrekt gehandhabt wird"""
    result = redact({})
    assert result == {}
