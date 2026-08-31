from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from scripts import build_openmaic_course as builder
from scripts import run_openmaic_course_demo as demo
from scripts import validate_openmaic_course as validator

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "docs/learning/courses/openmaic-ananta-codecompass"
DEPLOY = ROOT / "deploy/examples/openmaic-course"


def _json(relative: str):
    return json.loads((COURSE / relative).read_text(encoding="utf-8"))


def test_course_contract_is_german_bounded_and_offline_capable():
    course = _json("course.json")
    assert course["status"] == "ready"
    assert course["language"] == "de-DE"
    assert 45 <= course["duration_minutes"] <= 90
    assert len(course["learning_goals"]) == 5
    assert len(course["interaction_ids"]) >= 2
    assert "no-ananta-control-plane-connection" in course["security_boundaries"]
    assert (COURSE / course["offline_fallback"]).is_file()


def test_source_audit_is_revision_bound_without_invented_grounding_ids():
    source = _json("source-audit.json")
    snapshot = _json("demo/codecompass-snapshot.json")
    assert source["grounding_status"] == "unverified_missing_SRC_ids"
    assert source["source_ids"] == source["run_ids"] == []
    assert snapshot["source_ids"] == []
    assert snapshot["repository_revision"] == source["repository_revision"]
    assert validator._validate_sources(source, snapshot) >= 10
    for question in snapshot["questions"]:
        assert [item["level"] for item in question["journey"]] == [
            "system",
            "subsystem",
            "component",
            "file",
            "symbol",
        ]
        assert {item["channel"] for item in question["journey"]} <= {
            "graph",
            "symbol",
            "full_text",
            "vector",
        }


def test_course_contrasts_weak_retrieval_without_claiming_hallucination_elimination():
    snapshot = _json("demo/codecompass-snapshot.json")
    assert len(snapshot["questions"]) == 3
    assert all(item["weak_retrieval_answer"] for item in snapshot["questions"])
    all_text = " ".join(
        [
            (COURSE / "instructor-notes.md").read_text(encoding="utf-8"),
            (COURSE / "openmaic-prompt.md").read_text(encoding="utf-8"),
        ]
    ).lower()
    assert "halluzinationen" in all_text
    assert "vollständig" in all_text
    assert "falsch" in all_text or "niemals" in all_text


def test_openmaic_archive_matches_official_v1_import_shape_and_is_deterministic():
    expected = builder.expected_artifacts()
    assert all(path.read_bytes() == content for path, content in expected.items())
    archive_path = COURSE / "openmaic-ananta-codecompass.maic.zip"
    with ZipFile(archive_path) as archive:
        assert archive.namelist() == ["manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["formatVersion"] == 1
    assert manifest["appVersion"] == "1.0.0"
    assert len(manifest["scenes"]) == 10
    assert len([scene for scene in manifest["scenes"] if scene["type"] == "quiz"]) == 2
    assert len([scene for scene in manifest["scenes"] if scene["title"].startswith("Snapshot-Frage:")]) == 3
    assert all(scene["type"] == scene["content"]["type"] for scene in manifest["scenes"])


def test_automated_quiz_covers_hub_worker_rag_and_tool_authority():
    content = _json("openmaic-content.json")
    assert validator._validate_quiz(content) == 3
    questions = {
        question["id"]: question
        for interaction in content["interactions"]
        for question in interaction["questions"]
    }
    assert questions["delegation-owner"]["answer"] == ["B"]
    assert questions["rag-difference"]["answer"] == ["B"]
    assert questions["tool-authority"]["answer"] == ["A", "B", "D"]


def test_course_preflight_is_fully_automatic():
    report = validator.validate()
    assert report["status"] == "passed"
    assert report["human_interaction_required"] is False
    assert report["live_system_required"] is False


def test_demo_resolver_uses_approved_local_snapshot_or_deterministic_fallback(tmp_path):
    approved, mode, reason = demo.resolve_snapshot(COURSE / "demo/codecompass-snapshot.json")
    assert mode == "approved_local_snapshot"
    assert reason is None
    assert len(approved["questions"]) == 3

    fallback, mode, reason = demo.resolve_snapshot(tmp_path / "unavailable.json")
    assert mode == "offline_fallback"
    assert reason == "course_demo_snapshot_unavailable"
    assert fallback["snapshot_label"].startswith("OFFLINE SNAPSHOT")


@pytest.mark.parametrize(
    "unsafe_content,reason",
    [
        ("api_key=abcdefghijklmnop", "course_secret_pattern_detected"),
        ("siehe /home/private-user/work", "course_local_path_detected"),
        ("http://192.168.1.40/private", "course_private_url_detected"),
        ("person@example.invalid", "course_personal_data_detected"),
    ],
)
def test_preflight_rejects_sensitive_demo_material(monkeypatch, tmp_path, unsafe_content, reason):
    (tmp_path / "unsafe.md").write_text(unsafe_content, encoding="utf-8")
    monkeypatch.setattr(validator, "COURSE_ROOT", tmp_path)
    with pytest.raises(validator.CourseValidationError, match=reason):
        validator._validate_material_safety()


def test_openmaic_compose_is_pinned_loopback_only_and_separate_from_ananta():
    compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["openmaic-course"]
    assert service["build"]["context"].endswith(
        "OpenMAIC.git#aa2bfb3c1d406c47100c6744d90e788abdf1f6d5"
    )
    assert service["ports"] == ["127.0.0.1:${OPENMAIC_PORT:-3000}:3000"]
    assert service.get("networks") is None
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ANANTA" not in json.dumps(compose).upper()


def test_versioned_environment_template_contains_no_credentials():
    values = {}
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    assert values["OPENAI_API_KEY"] == ""
    assert values["ACCESS_CODE"] == ""
    assert values["DEFAULT_MODEL"] == ""
