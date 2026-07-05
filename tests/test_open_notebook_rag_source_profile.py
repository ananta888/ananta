import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.services.rag_source_profile_loader import RagSourceProfileLoader

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "domains" / "open_notebook" / "rag_sources" / "open_notebook_source.profile.json"
SCHEMA_PATH = ROOT / "schemas" / "domain" / "rag_source_profile.v1.json"

DESCRIPTORS = {
    "open_notebook": {
        "rag_profiles": ["domains/open_notebook/rag_sources/open_notebook_source.profile.json"],
    }
}


def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _loader() -> RagSourceProfileLoader:
    loader = RagSourceProfileLoader()
    loader.load_from_descriptors(DESCRIPTORS)
    return loader


def test_profile_validates_against_schema():
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert [error.message for error in validator.iter_errors(_profile())] == []


def test_profile_uses_accepted_ingestion_path():
    assert _profile()["ingestion_path"] in {"codecompass", "rag_helper", "codecompass/rag_helper"}


def test_allowed_paths_are_safe():
    for path in _profile()["allowed_paths"]:
        assert not path.startswith("/")
        assert ".." not in path


def test_globs_include_fixture_formats_and_exclude_secrets():
    profile = _profile()
    includes = " ".join(profile["include_globs"])
    assert "*.json" in includes
    assert "*.md" in includes
    excludes = " ".join(profile["exclude_globs"])
    assert "secret" in excludes
    assert ".env" in excludes


def test_loader_returns_profile_for_indexing():
    profiles = _loader().profiles_for_indexing(domain_id="open_notebook")
    assert [item["source_id"] for item in profiles] == ["open_notebook.imported.sources"]


def test_loader_prefers_profile_for_research_intents():
    loader = _loader()
    for intent in ("notebook question", "research summary", "source lookup", "notes review"):
        profiles = loader.profiles_for_retrieval("open_notebook", retrieval_intent=intent)
        assert profiles
        assert profiles[0]["source_id"] == "open_notebook.imported.sources"


def test_profile_declares_open_notebook_retrieval_source_type():
    assert "open_notebook" in _profile()["retrieval_source_types"]
