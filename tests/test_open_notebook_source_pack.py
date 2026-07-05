import json
from pathlib import Path

from agent.sources.source_registry import SourceRegistry, validate_source_pack_payload

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "sources" / "source-packs" / "open-notebook.source-pack.json"


def _pack() -> dict:
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def test_pack_validates_against_schema():
    assert validate_source_pack_payload(_pack()) == []


def test_pack_references_existing_files():
    pack = _pack()
    for source in pack["sources"]:
        descriptor_path = ROOT / source["descriptor_path"]
        assert descriptor_path.exists(), source["descriptor_path"]
        json.loads(descriptor_path.read_text(encoding="utf-8"))
    profile_ref = pack["extensions"]["rag_source_profile"]
    assert (ROOT / profile_ref).exists()
    json.loads((ROOT / profile_ref).read_text(encoding="utf-8"))


def test_pack_contains_bootstrap_instructions_for_offline_fixtures():
    instructions = _pack()["extensions"]["bootstrap_instructions"]
    joined = " ".join(instructions)
    assert "tests/fixtures/open_notebook" in joined
    assert "import-open-notebook" in joined


def test_pack_priority_rules_put_local_project_first():
    policy = _pack()["extensions"]["retrieval_priority_policy"]
    order = policy["priority_order"]
    assert order.index("local_project") < order.index("open_notebook_research")
    assert order.index("open_notebook_research") < order.index("general_knowledge")
    assert "research" in policy["condition"]


def test_pack_declares_trust_level_and_provenance_policy():
    pack = _pack()
    assert pack["sources"][0]["trust_level"] == "user_managed_research"
    provenance = pack["extensions"]["provenance_policy"]
    assert provenance["default_llm_scope"] == "local_only"
    assert provenance["chat_sessions_indexed"] is False


def test_pack_is_listed_by_source_registry(tmp_path):
    registry = SourceRegistry(root=tmp_path)
    packs = {item["source_pack_id"] for item in registry.list_source_packs()}
    assert "open-notebook" in packs
    pack = registry.get_source_pack("open-notebook")
    assert pack is not None
    assert pack["display_name"] == "OpenNotebook Research Source Pack"
