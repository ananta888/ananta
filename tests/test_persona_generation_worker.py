"""Closed Worker contract, replay and bounded local generator checks."""

import base64
import copy
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.persona_generation import (
    MAX_OUTPUT,
    RAW_VIDEO_BYTES,
    decode_result,
    recipe_digest,
    validate_assignment,
    validate_recipe,
)
from tests import test_persona_generated_source as generation_cases
from worker.meet_media.persona_generation_executor import PersonaGenerationExecutor
from worker.meet_media.persona_generation_frames import render
from worker.meet_media.persona_generator import VIDEO_COMMAND, ProceduralPersonaGenerator

generated = generation_cases.generated
pytestmark = pytest.mark.timeout(30)
RECIPE = {"profile": "procedural-avatar-v1", "media_kind": "image", "palette": "indigo"}


@pytest.fixture
def worker_case(generated, tmp_path):
    c = generated
    run = c.registry.reserve_run(
        tenant_id="tenant",
        project_id="project",
        task_id="generation-task",
        assignment_id="generation-assignment",
        dispatch_lease_id="generation-lease",
        repository_revision="1" * 40,
        input_digest=recipe_digest(RECIPE),
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
        source_ids=[p.source_id for p in c.output.source_pins()],
        evidence_scope="test",
        synthetic=True,
        idempotency_key="wire-generation-reservation",
    )
    evidence = c.registry.assignment_projection(
        tenant_id="tenant",
        project_id="project",
        run_id=run.run_id,
        task_id=run.task_id,
        assignment_id=run.assignment_id,
        dispatch_lease_id=run.dispatch_lease_id,
    )
    assignment = {
        "schema": "ananta.persona-generation-task.v1",
        "task_id": run.task_id,
        "assignment_id": run.assignment_id,
        "lease_id": run.dispatch_lease_id,
        "tenant_id": "tenant",
        "project_id": "project",
        "owner_subject": "owner",
        "run_id": run.run_id,
        "run_binding_digest": run.binding_digest,
        "admission_digest": "c" * 64,
        "source_sha256": run.input_digest,
        "deadline": int(time.time()) + 20,
        "evidence": evidence,
    }
    factory, guard = Mock(), Mock()
    factory.return_value.generate.return_value = render(RECIPE)
    guard_factory = Mock(return_value=guard)
    path = tmp_path / "worker-replay.db"
    executor = PersonaGenerationExecutor(path, guard_factory=guard_factory, generator=factory)
    return SimpleNamespace(
        assignment=assignment,
        request={"assignment": assignment, "recipe": dict(RECIPE)},
        executor=executor,
        factory=factory,
        guard=guard,
        guard_factory=guard_factory,
        path=path,
    )


def test_executor_requires_current_hub_lease_and_durably_consumes_one_assignment(worker_case):
    c = worker_case
    result = c.executor.execute(c.request)
    assert decode_result(result, c.assignment, RECIPE) == render(RECIPE)
    assert c.guard.require.call_count == 2
    assert c.factory.call_args.kwargs["require_current"] == c.guard.require
    with pytest.raises(ValueError, match="replayed"):
        c.executor.execute(c.request)
    restarted = PersonaGenerationExecutor(c.path, guard_factory=c.guard_factory, generator=c.factory)
    with pytest.raises(ValueError, match="replayed"):
        restarted.execute(c.request)
    c.factory.return_value.generate.assert_called_once()


@pytest.mark.parametrize("failure", ["guard", "generator", "result", "post_guard"])
def test_denial_or_bad_output_never_returns_media(worker_case, failure):
    c = worker_case
    if failure == "guard":
        c.guard.require.side_effect = PermissionError("revoked")
    elif failure == "generator":
        c.factory.return_value.generate.side_effect = ValueError("failed")
    elif failure == "result":
        c.factory.return_value.generate.return_value = b"not a PNG image"
    else:
        c.guard.require.side_effect = [None, PermissionError("revoked")]
    with pytest.raises((ValueError, PermissionError)):
        c.executor.execute(c.request)
    assert c.executor.lock.acquire(blocking=False)
    c.executor.lock.release()


@pytest.mark.parametrize(
    "change",
    [
        {"profile": "cloud"},
        {"media_kind": "voice"},
        {"palette": "https://foreign"},
        {"text": "prompt"},
        {"palette": []},
        {"palette": True},
        {"media_kind": None},
    ],
)
def test_closed_recipe_cannot_add_instructions_or_select_provider(worker_case, change):
    with pytest.raises(ValueError):
        worker_case.executor.execute(worker_case.request | {"recipe": RECIPE | change})
    worker_case.guard_factory.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "ananta.persona-image-task.v1"),
        ("lease_id", "old"),
        ("deadline", 0),
        ("deadline", True),
        ("admission_digest", "invalid"),
        ("source_sha256", "0" * 64),
    ],
)
def test_foreign_stale_or_changed_assignments_do_not_execute(worker_case, field, value):
    c = worker_case
    with pytest.raises(ValueError):
        c.executor.execute(c.request | {"assignment": c.assignment | {field: value}})
    c.guard_factory.assert_not_called()


def test_busy_worker_never_queues_independent_generation(worker_case):
    c = worker_case
    c.executor.lock.acquire()
    try:
        with pytest.raises(ValueError, match="busy"):
            c.executor.execute(c.request)
    finally:
        c.executor.lock.release()
    c.guard_factory.assert_not_called()


def test_evidence_projection_and_recipe_are_both_closed(worker_case):
    c = worker_case
    assignment = copy.deepcopy(c.assignment)
    assignment["evidence"]["source_ids"].append("SRC_unknown")
    with pytest.raises(ValueError):
        validate_assignment(assignment, time.time())
    with pytest.raises(ValueError):
        validate_recipe(RECIPE | {"extra": True})
    with pytest.raises(ValueError):
        c.executor.execute(c.request | {"publish": True})


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("palette", ["indigo", "teal", "amber"])
def test_procedural_renderer_is_bounded_repeatable_and_visibly_distinct(kind, palette):
    recipe = RECIPE | {"media_kind": kind, "palette": palette}
    content = render(recipe)
    assert content == render(recipe)
    if kind == "image":
        assert content.startswith(b"\x89PNG") and len(content) <= MAX_OUTPUT
    else:
        assert len(content) == RAW_VIDEO_BYTES
        assert content[: 256 * 256 * 3] != content[256 * 256 * 3 : 2 * 256 * 256 * 3]


@pytest.mark.parametrize("failure", ["exit", "size", "revoked", "frames"])
def test_supervisor_never_retries_bad_process_or_falls_back(failure):
    runner, check = Mock(), Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=b"invalid")
    if failure == "exit":
        runner.run.return_value.returncode = 1
    if failure == "size":
        runner.run.return_value.stdout = b"x" * (RAW_VIDEO_BYTES + 1)
    if failure == "revoked":
        check.side_effect = PermissionError("revoked")
    generator = ProceduralPersonaGenerator(
        require_current=check, deadline_monotonic=time.monotonic() + 15, runner=runner
    )
    with pytest.raises(ValueError, match="failed_or_revoked"):
        generator.generate(RECIPE | {"media_kind": "video"})
    assert runner.run.call_count <= 1


@pytest.mark.parametrize("deadline", [0, True, float("nan"), float("inf")])
def test_supervisor_deadline_is_strict_and_bounded(deadline):
    with pytest.raises(ValueError):
        ProceduralPersonaGenerator(require_current=lambda: None, deadline_monotonic=deadline)


def test_codec_and_pipeline_are_fixed_local_ports():
    assert VIDEO_COMMAND[0] == "/usr/bin/ffmpeg"
    assert VIDEO_COMMAND[VIDEO_COMMAND.index("-c:v") + 1] == "libx264"
    assert VIDEO_COMMAND[VIDEO_COMMAND.index("-threads") + 1] == "1"
    assert VIDEO_COMMAND[VIDEO_COMMAND.index("-protocol_whitelist") + 1] == "pipe"
    assert VIDEO_COMMAND[VIDEO_COMMAND.index("-frames:v") + 1] == "24"


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "foreign"},
        {"lease_id": "old"},
        {"media_type": "video/mp4"},
        {"content": []},
        {"content": "invalid"},
        {"extra": True},
    ],
)
def test_closed_result_rejects_assignment_media_and_shape_mutations(worker_case, change):
    c = worker_case
    result = {
        "task_id": c.assignment["task_id"],
        "lease_id": c.assignment["lease_id"],
        "media_type": "image/png",
        "content": base64.b64encode(render(RECIPE)).decode(),
    }
    with pytest.raises(ValueError):
        decode_result(result | change, c.assignment, RECIPE)


@pytest.mark.skipif(os.environ.get("PERSONA_GENERATION_NATIVE_GATE") != "1", reason="explicit native codec gate")
@pytest.mark.parametrize("kind", ["image", "video"])
def test_actual_supervised_local_generation_can_be_decoded_without_capture(kind):
    from tests.persona_native_codec_runner import NativePersonaCodecRunner
    from worker.meet_media.persona_image import sanitize_image
    from worker.meet_media.persona_video_inspector import PersonaVideoInspector

    runner = NativePersonaCodecRunner() if kind == "video" else None
    checks = Mock()
    recipe = RECIPE | {"media_kind": kind}
    content = ProceduralPersonaGenerator(
        require_current=checks,
        deadline_monotonic=time.monotonic() + 18,
        runner=runner,
    ).generate(recipe)
    if kind == "image":
        normalized = sanitize_image(content, "image/png")
        assert (normalized.width, normalized.height) == (256, 256)
    else:
        normalized = PersonaVideoInspector(
            require_current=checks,
            deadline_monotonic=time.monotonic() + 20,
            runner=runner,
        ).inspect(content, "video/mp4")
        assert normalized.frames == 24 and normalized.duration_ms == 2000
    assert checks.call_count >= 2
