"""Explicit operator policy, bounded parsing and configuration precedence."""

import json

import pytest
from flask import Flask

from agent.bootstrap.meet_companion_model_profile import CONFIG, ENVIRONMENT, configure_model_profile


def profile():
    return {"backend": "openai", "model": "synthetic-model", "environment_digest": "e" * 64,
            "auto_execute": True}


@pytest.mark.parametrize("auto_execute", [True, False])
def test_explicit_environment_profile_keeps_operator_auto_execution_choice(monkeypatch, auto_execute):
    app = Flask(__name__)
    value = profile() | {"auto_execute": auto_execute}
    monkeypatch.setenv(ENVIRONMENT, json.dumps(value))
    configure_model_profile(app)
    assert app.config[CONFIG] == value


@pytest.mark.parametrize("raw", ['{"model":"first","model":"second"}', 'NaN', 'x' * 2049,
    json.dumps(profile() | {"endpoint": "http://untrusted.test"}),
    json.dumps(profile() | {"auto_execute": 1}), json.dumps(profile() | {"environment_digest": "unknown"})])
def test_malformed_unknown_or_implicit_auto_policy_is_rejected_without_private_value(monkeypatch, raw):
    app = Flask(__name__)
    monkeypatch.setenv(ENVIRONMENT, raw)
    with pytest.raises(ValueError, match="^meet_companion_model_profile_config_invalid$"):
        configure_model_profile(app)
    assert CONFIG not in app.config


def test_explicit_application_profile_takes_precedence_over_environment(monkeypatch):
    app = Flask(__name__)
    app.config[CONFIG] = profile()
    monkeypatch.setenv(ENVIRONMENT, "invalid_environment_ignored")
    configure_model_profile(app)
    assert app.config[CONFIG] == profile()


def test_absent_profile_does_not_infer_worker_defaults_or_approval(monkeypatch):
    app = Flask(__name__)
    monkeypatch.delenv(ENVIRONMENT, raising=False)
    configure_model_profile(app)
    assert CONFIG not in app.config
