"""HDE-012: persistent dynamic tool registry."""
import pytest

from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


def _spec(name="custom.count_lines", **overrides):
    spec = {
        "name": name,
        "description": "Count lines",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["wc", "-l", "{path}"],
        "intent_aliases": ["zähle zeilen"],
    }
    spec.update(overrides)
    return spec


def _store(registry, name="custom.count_lines", **kwargs):
    defaults = {
        "spec": _spec(name),
        "proposal_digest": "digest-1",
        "validated_digest": "digest-1",
        "validation_report_ref": "tool-proposals/reports/digest-1.json",
        "approval_status": "granted",
    }
    defaults.update(kwargs)
    return registry.store_promoted_tool(name=name, **defaults)


def test_store_and_load_active_tool(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    record = _store(registry)
    assert record["version"] == 1
    assert record["status"] == "active"
    loaded = registry.get_active_tool("custom.count_lines")
    assert loaded is not None
    assert loaded["spec"]["command_template"] == ["wc", "-l", "{path}"]


def test_only_active_and_granted_tools_are_offered(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry)
    registry.set_status("custom.count_lines", "disabled")
    assert registry.get_active_tool("custom.count_lines") is None
    assert registry.list_active_tools() == []


def test_static_registry_names_cannot_be_shadowed(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    with pytest.raises(ValueError, match="dynamic_tool_shadows_static_tool"):
        registry.store_promoted_tool(
            name="repo.grep",
            spec=_spec("repo.grep"),
            proposal_digest="d",
            validated_digest="d",
            validation_report_ref=None,
            approval_status="granted",
        )


def test_snapshot_contains_source_version_digest_no_internals(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry)
    snapshot = registry.registry_snapshot()
    row = snapshot["tools"][0]
    assert row["source"] == "dynamic"
    assert row["version"] == 1
    assert row["proposal_digest"] == "digest-1"
    assert "command_template" not in row
    assert "spec" not in row


def test_list_active_tools_is_sorted(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, name="custom.zzz")
    _store(registry, name="custom.aaa")
    names = [row["name"] for row in registry.list_active_tools()]
    assert names == ["custom.aaa", "custom.zzz"]


def test_versions_accumulate_on_replacement(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry)
    record = _store(registry, proposal_digest="digest-2", validated_digest="digest-2")
    assert record["version"] == 2
    assert len(record["versions"]) == 1
    assert record["versions"][0]["proposal_digest"] == "digest-1"


def test_invalid_names_are_refused(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    with pytest.raises(ValueError, match="invalid_dynamic_tool_name"):
        registry.store_promoted_tool(
            name="../evil",
            spec=_spec(),
            proposal_digest="d",
            validated_digest="d",
            validation_report_ref=None,
            approval_status="granted",
        )


def test_registry_writes_use_atomic_writer(tmp_path, monkeypatch):
    import agent.services.dynamic_tool_registry_service as registry_module

    writes = []
    original = registry_module._atomic_write_json

    def _recording_write(path, payload):
        writes.append((path, payload.get("name"), payload.get("version")))
        original(path, payload)

    monkeypatch.setattr(registry_module, "_atomic_write_json", _recording_write)
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, name="custom.atomic")
    registry.record_usage("custom.atomic", success=True)
    registry.set_status("custom.atomic", "disabled")
    assert [row[1] for row in writes] == ["custom.atomic", "custom.atomic", "custom.atomic"]
    assert registry.get_record("custom.atomic")["status"] == "disabled"
