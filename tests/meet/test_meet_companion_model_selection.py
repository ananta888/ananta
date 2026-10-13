"""Hub-owned signed model selection; synthetic Task/source/model-policy fixtures."""

import pytest

from ananta_contracts.meet_companion_task_grant import validate_response
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import assignment
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import KEY
from tests.meet.test_meet_companion_task_tools import hub as hub
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient


def configure(f, model_service):
    service = f.assigned[1]
    service.model_policy = model_service.policy
    f.app.extensions["meet_companion_task_grants"] = service
    return service


def test_actual_signed_grant_selects_only_hub_authorized_model_profile(models):
    f, model_service = models
    configure(f, model_service)
    full = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    with running_hub(f.app) as endpoint:
        client = AssignedCompanionGrantClient(full, endpoint, KEY)
        result = client.read()
    assert result["model_execution"] == {"backend": "openai", "model": "synthetic-model",
                                         "environment_digest": "e" * 64}


def test_missing_model_policy_preserves_legacy_grant_shape(models):
    f, _model_service = models
    with f.app.app_context():
        result = f.assigned[1].issue(f.request["assignment"])
    assert "model_execution" not in result


def test_policy_withdrawal_does_not_downgrade_to_legacy_model_selection(models):
    f, model_service = models
    service = configure(f, model_service)
    f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["auto_execute"] = False
    with f.app.app_context(), pytest.raises(ValueError, match="profile_denied"):
        service.issue(f.request["assignment"])


def test_profile_change_while_signing_cannot_publish_old_selection(models, monkeypatch):
    f, model_service = models
    service = configure(f, model_service)
    original = service.issuer.issue_dialog

    def changed(*args):
        grant = original(*args)
        f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["model"] = "replacement"
        return grant

    monkeypatch.setattr(service.issuer, "issue_dialog", changed)
    with f.app.app_context(), pytest.raises(ValueError, match="profile_changed"):
        service.issue(f.request["assignment"])


def test_policy_port_removal_while_signing_cannot_publish_old_selection(models, monkeypatch):
    f, model_service = models
    service = configure(f, model_service)
    original = service.issuer.issue_dialog

    def changed(*args):
        grant = original(*args)
        service.model_policy = None
        return grant

    monkeypatch.setattr(service.issuer, "issue_dialog", changed)
    with f.app.app_context(), pytest.raises(ValueError, match="profile_changed"):
        service.issue(f.request["assignment"])


@pytest.mark.parametrize("field,value", [("endpoint", "foreign"), ("model", ""),
                                        ("backend", "shell"), ("environment_digest", "unverified")])
def test_worker_rejects_broadened_or_invalid_profile_metadata(models, field, value):
    f, model_service = models
    service = configure(f, model_service)
    with f.app.app_context():
        result = service.issue(f.request["assignment"])
    result["model_execution"][field] = value
    full = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    with pytest.raises(ValueError, match="model_profile_invalid"):
        validate_response(result, full, f.f.now)
