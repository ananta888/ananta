from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_DOCS = [
    "README.md",
    "docs/setup/bootstrap-install.md",
    "docs/setup/quickstart.md",
    "docs/setup/ananta_init.md",
    "docs/cli/commands.md",
    "docs/golden-path-cli.md",
    "docs/demo-flows.md",
]
DEV_FALLBACK_DOC = "docs/cli/developer_entrypoints.md"

FENCE_PATTERN = re.compile(r"```(?:bash|powershell)?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _command_lines(path: str) -> list[str]:
    content = _read(path)
    commands: list[str] = []
    for block in FENCE_PATTERN.findall(content):
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            commands.append(line)
    return commands


def test_user_docs_use_known_ananta_commands_and_no_module_fallback() -> None:
    contract = json.loads(_read("docs/status/documentation-command-contract.json"))
    allowed = set(contract["entrypoints"]["user_path"]["core_commands"]) | set(
        contract["entrypoints"]["user_path"]["compatibility_commands"]
    )

    for doc in USER_DOCS:
        for line in _command_lines(doc):
            if line.startswith("python -m agent.cli_goals"):
                raise AssertionError(f"{doc}: user docs must not default to module fallback command: {line}")
            if not line.startswith("ananta "):
                continue
            parts = line.rstrip("\\").strip().split()
            assert len(parts) >= 2, f"{doc}: invalid ananta command line: {line}"
            assert parts[1] in allowed, f"{doc}: unknown ananta subcommand '{parts[1]}' in '{line}'"


def test_user_docs_openai_init_uses_endpoint_url_flag() -> None:
    for doc in USER_DOCS:
        for line in _command_lines(doc):
            if not line.startswith("ananta init"):
                continue
            if "--llm-backend openai-compatible" not in line:
                continue
            assert "--endpoint-url" in line, f"{doc}: openai-compatible init must use --endpoint-url ({line})"
            assert "--base-url" not in line, f"{doc}: openai-compatible init must not use --base-url ({line})"


def test_developer_fallback_doc_keeps_module_entrypoint_contract() -> None:
    commands = _command_lines(DEV_FALLBACK_DOC)
    module_lines = [line for line in commands if line.startswith("python -m agent.cli_goals")]
    assert module_lines, "developer fallback doc must keep python -m agent.cli_goals examples"
    for line in module_lines:
        assert "--status" in line or "--first-run" in line or "ask " in line
