from agent.common.redaction import redact, VisibilityLevel, SensitiveDataClass

def test_visibility_levels():
    data = {
        "password": "secret_password",
        "path": "/etc/shadow",
        "hub_url": "http://internal-hub:5000",
        "ip": "10.0.0.1",
        "username": "normal_user"
    }

    # PUBLIC Level: Alles sensible sollte maskiert sein
    public_res = redact(data, visibility=VisibilityLevel.PUBLIC)
    assert "***REDACTED_SECRET***" in public_res["password"]
    assert "***REDACTED_PATH***" in public_res["path"]
    assert "***REDACTED_INTERNAL_URL***" in public_res["hub_url"]
    assert "***REDACTED_IP_ADDRESS***" in public_res["ip"]
    assert public_res["username"] == "normal_user"

    # USER Level (Default): IP meist sichtbar, Rest maskiert
    user_res = redact(data, visibility=VisibilityLevel.USER)
    assert "***REDACTED_SECRET***" in user_res["password"]
    assert "***REDACTED_PATH***" in user_res["path"]
    assert "***REDACTED_INTERNAL_URL***" in user_res["hub_url"]
    assert user_res["ip"] == "10.0.0.1" # In meiner Impl: USER darf IP sehen

    # ADMIN Level: Pfade und URLs sichtbar, Secrets maskiert
    admin_res = redact(data, visibility=VisibilityLevel.ADMIN)
    assert "***REDACTED_SECRET***" in admin_res["password"]
    assert admin_res["path"] == "/etc/shadow"
    assert admin_res["hub_url"] == "http://internal-hub:5000"
    assert admin_res["ip"] == "10.0.0.1"

    # DEBUG Level: Alles sichtbar
    debug_res = redact(data, visibility=VisibilityLevel.DEBUG)
    assert debug_res["password"] == "secret_password"
    assert debug_res["path"] == "/etc/shadow"


def test_redact_special_patterns():
    # OpenAI Key
    s1 = "My key is sk-1234567890abcdef1234567890abcdef"
    assert "sk-" not in redact(s1)
    assert "***" in redact(s1)

    # AWS Key
    s2 = "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF"
    assert "AKIA" not in redact(s2)
    assert "***" in redact(s2)


def test_redact_pydantic_like_objects():
    class FakeModel:
        def model_dump(self):
            return {"password": "123", "name": "test"}

    m = FakeModel()
    res = redact(m)
    assert res["password"] == "***REDACTED_SECRET***"
    assert res["name"] == "test"
