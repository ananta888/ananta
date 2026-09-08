"""Closed historical metadata never authorizes current media or broadens legacy assignments."""

import copy
import time
from types import SimpleNamespace

import pytest

from agent.common.meet_task_write_validation import meet_task_write_error
from agent.models.meet_dialog_phase import phase_binding
from agent.models.meet_preauthorization_binding import assignment_projection
from ananta_contracts.meet_dialog import validate_assignment
from ananta_contracts.meet_initial_persona import initial_projection, validate_initial_persona
from tests.test_meet_dialog_avatar_selection import image_selection
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_preauthorization_policy import context


def projection(kind="image"):
    selection = image_selection()
    selection["reference"] |= {"kind": kind}
    selection["mode"] = "persona-" + kind + "-v1"
    if kind == "video":
        selection["repeat_mode"] = "hold_last"
    return initial_projection({"voice" if kind == "voice" else "avatar": selection})


@pytest.mark.parametrize("kind", ["image", "video", "voice"])
def test_exact_reference_and_explicit_negotiations_are_required_before_worker_acceptance(kind):
    old = assignment() | {
        "capabilities": ["avatar.publish", "speech.publish"],
        "avatar_images": True,
        "avatar_videos": True,
        "voice_profiles": True,
    }
    value = old | {"initial_persona": projection(kind)}
    assert validate_assignment(value, time.time()) is value
    assert "profile" not in next(v for k, v in value["initial_persona"].items() if k != "schema")
    denied = "voice_profiles" if kind == "voice" else "avatar_videos" if kind == "video" else "avatar_images"
    invalid = copy.deepcopy(value)
    invalid.pop(denied)
    with pytest.raises(ValueError):
        validate_assignment(invalid, time.time())
    assert validate_assignment(old, time.time()) is old and "initial_persona" not in old


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "unknown",
        "null",
        "scope",
        "bool_revision",
        "float_revision",
        "hash",
        "bytes",
        "profile",
        "digest",
        "neutral",
        "repeat",
        "voice_kind",
    ],
)
def test_closed_projection_rejects_undeclared_or_malformed_metadata(mutation):
    value = projection("video")
    row = value["avatar"]
    if mutation == "empty":
        value = {"schema": value["schema"]}
    elif mutation == "unknown":
        value["schema"] = "future"
    elif mutation == "null":
        value = None
    elif mutation == "scope":
        row["reference"]["project_id"] = "foreign"
    elif mutation == "bool_revision":
        row["reference"]["revision"] = True
    elif mutation == "float_revision":
        row["reference"]["revision"] = 1.0
    elif mutation == "hash":
        row["reference"]["sha256"] = "x" * 64
    elif mutation == "bytes":
        row["mp4"] = "AAAA"
    elif mutation == "profile":
        row["profile"] = {"owner_id": "private"}
    elif mutation == "digest":
        row["selection_digest"] = ""
    elif mutation == "neutral":
        row["mode"] = "neutral-ai-v1"
    elif mutation == "repeat":
        row["repeat_mode"] = True
    else:
        value["voice"] = value.pop("avatar")
    with pytest.raises(ValueError):
        validate_initial_persona(
            value, "tenant", "project", avatar_images=True, avatar_videos=True, voice_profiles=True
        )


@pytest.mark.parametrize("mutation", ["missing", "null", "outer", "digest", "boolean", "float"])
def test_original_task_metadata_cannot_be_stripped_retrofitted_or_coerced(mutation):
    row = SimpleNamespace(
        task_kind="meet_dialog_session",
        status="in_progress",
        worker_execution_context={"meet_dialog": {"initial_persona": projection()}},
    )
    other = copy.deepcopy(row)
    context = other.worker_execution_context["meet_dialog"]
    if mutation == "missing":
        del context["initial_persona"]
    elif mutation == "null":
        context["initial_persona"] = None
    elif mutation == "outer":
        other.worker_execution_context = []
    elif mutation == "digest":
        context["initial_persona"]["avatar"]["selection_digest"] = "b" * 64
    else:
        context["initial_persona"]["avatar"]["reference"]["revision"] = True if mutation == "boolean" else 1.0
    assert meet_task_write_error(row, other) == "meet_dialog_initial_persona_immutable"
    assert meet_task_write_error(other, row) == "meet_dialog_initial_persona_immutable"
    assert meet_task_write_error(row, copy.deepcopy(row)) is None


def test_original_identity_binds_initial_pin_without_freezing_later_explicit_source_choices():
    before = context() | {"capabilities": ["avatar.publish"], "avatar_selection": image_selection()}
    after = before | {"initial_persona": projection()}
    args = ("task", "tenant", "project")
    old = assignment_projection(*args, "https://meet.test", before)
    new = assignment_projection(*args, "https://meet.test", after)
    assert new == old | {"initial_persona": projection()}
    assert phase_binding(*args, before) != phase_binding(*args, after)
    changed = after | {"avatar_selection": {"mode": "neutral-ai-v1"}}
    assert assignment_projection(*args, "https://meet.test", changed) == new
    assert phase_binding(*args, changed) == phase_binding(*args, after)
