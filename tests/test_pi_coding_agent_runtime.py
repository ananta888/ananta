"""Pinned runtime layout and closed target projection, without installed Node."""

import json
from dataclasses import replace

import pytest

from agent.cli_backends.coding_agent_targets import CodingAgentInferenceTarget
from agent.cli_backends.pi_configuration import validate_pi_target
from agent.cli_backends.pi_runtime import pi_sdk_command


@pytest.fixture
def package(tmp_path, monkeypatch):
    package = tmp_path / "package"
    binary = package / "dist" / "bundle" / "cli.js"
    binary.parent.mkdir(parents=True)
    binary.write_text("// deterministic layout fixture\n")
    (package / "dist" / "index.js").write_text("// deterministic SDK fixture\n")
    (package / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent", "version": "0.85.1"}))
    monkeypatch.setattr("agent.cli_backends.pi_runtime.shutil.which", lambda name, **_: "/pinned/node")
    return package, binary


def test_pi_sdk_uses_absolute_node_and_sibling_pinned_sdk(package):
    root, binary = package
    argv = pi_sdk_command(str(binary))
    assert argv[0] == "/pinned/node"
    assert argv[1].endswith("/agent/cli_backends/pi_sdk_entry.mjs")
    assert argv[2] == str(root / "dist" / "index.js")


@pytest.mark.parametrize("metadata", [[], {}, {"name": "another-package", "version": "0.85.1"},
                                     {"name": "@earendil-works/pi-coding-agent", "version": "0.85.2"}])
def test_pi_sdk_rejects_changed_package_identity(package, metadata):
    root, binary = package
    (root / "package.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="pi_runtime_metadata_invalid"):
        pi_sdk_command(str(binary))


def test_pi_sdk_rejects_external_sdk_symlink(package, tmp_path):
    root, binary = package
    outside = tmp_path / "outside.js"
    outside.write_text("// foreign SDK fixture\n")
    sdk = root / "dist" / "index.js"
    sdk.unlink()
    sdk.symlink_to(outside)
    with pytest.raises(ValueError, match="pi_runtime_layout_unverified"):
        pi_sdk_command(str(binary))


def test_pi_sdk_missing_node_is_bounded_error(package, monkeypatch):
    monkeypatch.setattr("agent.cli_backends.pi_runtime.shutil.which", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="node_runtime_unavailable"):
        pi_sdk_command(str(package[1]))


@pytest.mark.parametrize("changes,reason", [
    ({"client_id": "other"}, "pi_target_unsupported"),
    ({"model": "--help"}, "pi_model_invalid"), ({"model": "bad\nmodel"}, "pi_model_invalid"),
    ({"base_url": "file:///secret"}, "pi_endpoint_invalid"),
    ({"base_url": "http://host:bad/v1"}, "pi_endpoint_invalid"),
    ({"base_url": "http://host/v1?secret=x"}, "pi_endpoint_invalid"),
    ({"base_url": "http://host/v1#fragment"}, "pi_endpoint_invalid"),
    ({"provider_id": "openrouter"}, "pi_endpoint_invalid"),
    ({"api_key": "line\nbreak"}, "pi_auth_required"),
])
def test_pi_target_rejects_unclosed_endpoint_model_or_credentials(changes, reason):
    target = CodingAgentInferenceTarget(
        client_id="pi", provider_id="ollama", model="selected", cli_model="selected",
        base_url="http://model-worker:11434/v1", target_kind="local_openai", api_key="synthetic",
    )
    assert validate_pi_target(replace(target, **changes)) == reason
