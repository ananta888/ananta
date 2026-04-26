from flask import Flask

from agent.common.errors import api_response, user_error_guidance


def test_user_error_guidance_accepts_string_status_code():
    guidance = user_error_guidance(code="500", message="internal_failure")

    assert guidance["summary"] == "Der Hub konnte die Anfrage gerade nicht verarbeiten."
    assert guidance["next_steps"]


def test_api_response_normalizes_string_status_code():
    app = Flask(__name__)

    with app.app_context():
        response, status_code = api_response(status="error", message="validation_failed", data={}, code="422")

    assert status_code == 422
    assert response.get_json()["data"]["error_help"]["summary"]


def test_api_response_falls_back_to_500_for_invalid_error_status_code():
    app = Flask(__name__)

    with app.app_context():
        _response, status_code = api_response(status="error", message="boom", data={}, code="not-a-code")

    assert status_code == 500
