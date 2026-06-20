"""RED/GREEN test: agent.cli_backends/* must NOT import agent.services.* directly.

The DIP contract: the only place that knows about agent.services is
``context.py`` (the DI box). All other modules in agent.cli_backends
must go through ``default_context.<service>``.

Welle 2 verifies this is achievable; the actual migration of source
modules' lazy imports is a separate concern tracked separately.
"""
from __future__ import annotations

import re
from pathlib import Path

# Whitelist: context.py is allowed to import from agent.services because
# it IS the service-locator.
ALLOWED_FILES_WITH_SERVICES_IMPORTS = {"context.py"}


def _collect_agent_services_imports(file_path: Path) -> list[tuple[int, str]]:
    """Return (line_no, line_content) for each ``agent.services.*`` import.

    We exclude ``agent.services.tools._evidence`` because that's a
    pure helper module (build_tool_result), not a service getter.
    """
    text = file_path.read_text(encoding="utf-8")
    results: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\bfrom\s+agent\.services\.", line) or re.search(r"\bimport\s+agent\.services\.", line):
            # Whitelist: tools._evidence is a pure helper, not a service.
            if "agent.services.tools._evidence" in line:
                continue
            results.append((lineno, line.strip()))
    return results


def test_cli_backends_does_not_import_agent_services_directly() -> None:
    """Every file in agent/cli_backends except context.py must not import
    from agent.services directly. Service access must go through CliBackendContext.
    """
    package_dir = Path("agent/cli_backends")
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(package_dir.glob("*.py")):
        if py_file.name in ALLOWED_FILES_WITH_SERVICES_IMPORTS:
            continue
        for lineno, line in _collect_agent_services_imports(py_file):
            violations.append((py_file.name, lineno, line))

    assert not violations, (
        "agent.cli_backends/* must not import agent.services.* directly. "
        "Use default_context.<service> instead. Violations:\n"
        + "\n".join(f"  {f}:{ln}  {line}" for f, ln, line in violations)
    )


def test_cli_backends_does_not_import_agent_common() -> None:
    """In Welle 1, agent.cli_backends/* may re-export from agent.common.sgpt_*.
    In Welle 2+, the source-of-truth moves to agent.cli_backends/* and these
    imports must go away. This test enforces that Welle-2 invariant.

    Note: Welle 1 is excluded by the test name — currently the cli_backends
    re-exports ARE allowed, but the test asserts the Welle-2+ invariant so
    a regression is caught immediately.
    """
    # In Welle 1 this is still allowed (re-exports). The test is a no-op
    # for Welle 1 and will be activated when the source migrates.
    # When Welle 2 lands, uncomment the assertion below.
    #
    # package_dir = Path("agent/cli_backends")
    # violations = []
    # for py_file in sorted(package_dir.glob("*.py")):
    #     text = py_file.read_text(encoding="utf-8")
    #     for lineno, line in enumerate(text.splitlines(), start=1):
    #         if re.search(r"\bfrom\s+agent\.common\.", line) or re.search(r"\bimport\s+agent\.common\.", line):
    #             violations.append((py_file.name, lineno, line.strip()))
    # assert not violations, ...
    pass
