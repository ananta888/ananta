from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.rag_source_profile_loader import RagSourceProfileLoader

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "rag_source_profile.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _profile(domain_id: str = "example") -> dict:
    return {
        "schema": "rag_source_profile.v1",
        "source_id": "example.api.docs",
        "domain_id": domain_id,
        "source_type": "api_docs",
        "repo": "ananta888/ananta",
        "path": "docs",
        "ref": "main",
        "allowed_paths": ["docs/"],
        "include_globs": ["docs/**/*.md"],
        "exclude_globs": ["docs/**/draft/**"],
        "license_note": "Internal docs are used under project policy.",
        "intended_usage": "domain architecture and API usage guidance",
        "indexing_config_ref": "rag-helper/spring-large-project-profile-default.json",
        "ingestion_path": "codecompass/rag_helper",
        "retrieval_source_types": ["artifact"],
        "provenance": {
            "owner": "ananta",
            "captured_at": "2026-04-25T15:35:35+02:00",
            "explanation": "Profile tracks architecture and API docs for domain retrieval.",
        },
    }


def test_rag_source_profile_loader_loads_descriptor_referenced_profiles(tmp_path: Path) -> None:
    profile_path = tmp_path / "domains" / "example" / "rag_sources" / "api.profile.json"
    _write_json(profile_path, _profile())
    descriptors = {"example": {"domain_id": "example", "rag_profiles": [str(profile_path.relative_to(tmp_path))]}}
    loader = RagSourceProfileLoader(schema_path=SCHEMA_PATH, repository_root=tmp_path)

    loaded = loader.load_from_descriptors(descriptors)

    assert len(loaded["example"]) == 1
    assert loader.profiles_for_indexing(domain_id="example")[0]["source_id"] == "example.api.docs"
    assert loader.profiles_for_retrieval("example", retrieval_intent="api architecture docs", max_profiles=3)


def test_rag_source_profile_loader_rejects_unknown_or_mismatched_domain_reference(tmp_path: Path) -> None:
    profile_path = tmp_path / "domains" / "example" / "rag_sources" / "unknown-domain.profile.json"
    _write_json(profile_path, _profile(domain_id="other"))
    descriptors = {"example": {"domain_id": "example", "rag_profiles": [str(profile_path.relative_to(tmp_path))]}}
    loader = RagSourceProfileLoader(schema_path=SCHEMA_PATH, repository_root=tmp_path)

    with pytest.raises(ValueError, match="unknown domain_id|domain mismatch"):
        loader.load_from_descriptors(descriptors)


def test_rag_source_profile_loader_rejects_malformed_globs(tmp_path: Path) -> None:
    payload = _profile()
    payload["include_globs"] = ["[broken"]
    profile_path = tmp_path / "domains" / "example" / "rag_sources" / "broken.profile.json"
    _write_json(profile_path, payload)
    descriptors = {"example": {"domain_id": "example", "rag_profiles": [str(profile_path.relative_to(tmp_path))]}}
    loader = RagSourceProfileLoader(schema_path=SCHEMA_PATH, repository_root=tmp_path)

    with pytest.raises(ValueError, match="malformed glob"):
        loader.load_from_descriptors(descriptors)


def test_rag_source_profile_loader_rejects_missing_indexing_config(tmp_path: Path) -> None:
    payload = _profile()
    payload.pop("indexing_config_ref")
    profile_path = tmp_path / "domains" / "example" / "rag_sources" / "missing-index.profile.json"
    _write_json(profile_path, payload)
    descriptors = {"example": {"domain_id": "example", "rag_profiles": [str(profile_path.relative_to(tmp_path))]}}
    loader = RagSourceProfileLoader(schema_path=SCHEMA_PATH, repository_root=tmp_path)

    with pytest.raises(ValueError, match="indexing_config_ref|indexing config"):
        loader.load_from_descriptors(descriptors)

