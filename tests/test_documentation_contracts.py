"""Documentation contracts: the docs that must exist and the statements they must (not) contain.

One table instead of a test module per document: a documented command, flag or safety statement that
disappears or drifts fails here with the document and the phrase. Checks that need logic (command
contracts, track inventories) stay with their own tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DocContract:
    path: str
    contains: tuple[str, ...] = ()
    contains_casefold: tuple[str, ...] = ()  # compared case-insensitively
    absent: tuple[str, ...] = ()
    min_chars: int = 0


REQUIRED_PATHS = (
    "docs/security_baseline.md",
    "docs/hub_fallback.md",
    "docs/execution_scope.md",
    "docs/artifacts_and_routing.md",
    "docs/frontend_goal_ux.md",
    "docs/operator-tui-mouse-snake.md",
    "docs/status/active_and_completed_tracks.md",
    "docs/status/documentation-command-contract.json",
    "docs/status/documentation-command-usage.md",
    "docs/status/documentation-drift-decision-matrix.md",
    # bootstrap / install
    "scripts/install-ananta.ps1",
    "scripts/install-ananta.sh",
    "docs/setup/bootstrap-install.md",
    "docs/setup/ananta_update.md",
    # client surfaces: every documented command's entrypoint
    "client_surfaces/tui_runtime/ananta_tui/__main__.py",
    "scripts/smoke_tui_runtime.py",
    "scripts/smoke_nvim_runtime.py",
    "scripts/build_eclipse_runtime_plugin.py",
    "scripts/smoke_eclipse_runtime_bootstrap.py",
    "scripts/smoke_eclipse_runtime_headless.py",
    "scripts/run_client_surface_test_gate.py",
    # CodeCompass x86 core extension (X86CC-001)
    "docs/codecompass-x86-assembly-core-extension.md",
)

CONTRACTS = (
    DocContract(
        "docs/setup/bootstrap-install.md",
        contains=(
            "scripts/install-ananta.ps1",
            "scripts/install-ananta.sh",
            "raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.ps1",
            "raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.sh",
            "Get-Content .\\install-ananta.ps1",
            "sed -n '1,200p' install-ananta.sh",
            "ananta init",
            "ananta doctor",
            "ananta status",
            "ananta update --help",
            "--endpoint-url",
        ),
        contains_casefold=("local CLI usage does **not** require Docker",),
        absent=("--base-url",),
    ),
    DocContract("scripts/install-ananta.sh", contains=("--endpoint-url",), absent=("--base-url",)),
    DocContract("scripts/install-ananta.ps1", contains=("--endpoint-url",), absent=("--base-url",)),
    DocContract("docs/setup/ananta_update.md", contains_casefold=("rollback",)),
    DocContract("docs/setup/quickstart.md", contains=("docs/setup/bootstrap-install.md",)),
    DocContract("README.md", contains=("docs/setup/bootstrap-install.md",)),
    DocContract(
        "docs/tui-user-operator-guide.md", contains=("python -m client_surfaces.tui_runtime.ananta_tui --fixture",)
    ),
    DocContract("docs/nvim-plugin-user-guide.md", contains=("python3 scripts/smoke_nvim_runtime.py",)),
    DocContract("docs/plugin-vs-tui-usage-guide.md", contains=("Vim compatibility: deferred",)),
    DocContract(
        "docs/eclipse-plugin-runtime-bootstrap.md",
        contains=(
            "python3 scripts/build_eclipse_runtime_plugin.py --mode build",
            "python3 scripts/smoke_eclipse_runtime_bootstrap.py",
            "python3 scripts/smoke_eclipse_runtime_headless.py",
        ),
    ),
    DocContract(
        "docs/release-golden-path.md",
        contains=(
            "python3 scripts/run_client_surface_test_gate.py --out ci-artifacts/client-surface-test-gate.json",
        ),
    ),
    DocContract(
        # X86CC-001: scope, non-goals, schema, safety and the master feature flag
        "docs/codecompass-x86-assembly-core-extension.md",
        min_chars=800,
        contains=(
            "ANANTA_CODECOMPASS_X86_ENABLED",
            "Architecture",
            "Non-Goals",
            "Node Schema",
            "Edge Schema",
            "LocationRef",
            "Safety Policy",
            "Feature Flags",
        ),
        contains_casefold=("scope", "non-goal", "execution", "malware", "core"),
    ),
)


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_documented_path_exists(path: str) -> None:
    assert (ROOT / path).exists(), f"missing: {path}"


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda contract: contract.path)
def test_document_keeps_its_contract(contract: DocContract) -> None:
    text = (ROOT / contract.path).read_text(encoding="utf-8")
    folded = text.casefold()
    assert len(text) >= contract.min_chars, f"{contract.path}: suspiciously short ({len(text)} chars)"
    missing = [phrase for phrase in contract.contains if phrase not in text]
    missing += [phrase for phrase in contract.contains_casefold if phrase.casefold() not in folded]
    assert not missing, f"{contract.path} no longer states: {missing}"
    present = [phrase for phrase in contract.absent if phrase in text]
    assert not present, f"{contract.path} must not contain: {present}"
