"""Pattern gate service (PAT-017).

Deterministic structural checks that verify generated code fulfills the
chosen pattern's structural contract.  The gate does NOT compile or run
code — it operates on file contents, checking for:

- Required role files exist
- Pattern-specific structural markers are present (interface/protocol,
  context class, concrete implementations, test file)
- Test file exists when require_tests is True

Each checker returns a GateResult with passed/failed check details and
a remediation hint.  Results are referrable from workflow.steps[].checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class CheckDetail:
    name: str
    passed: bool
    message: str = ""
    remediation: str = ""


@dataclass
class GateResult:
    pattern_id: str
    language: str
    passed: bool
    checked_files: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    details: list[CheckDetail] = field(default_factory=list)
    remediation_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "passed": self.passed,
            "checked_files": self.checked_files,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "details": [
                {"name": d.name, "passed": d.passed, "message": d.message, "remediation": d.remediation}
                for d in self.details
            ],
            "remediation_hint": self.remediation_hint,
        }


# ---------------------------------------------------------------------------
# File content helpers
# ---------------------------------------------------------------------------

def _read_files(root: Path, paths: list[str]) -> dict[str, str]:
    """Return {rel_path: content} for all existing files; missing files map to ''."""
    result: dict[str, str] = {}
    for p in paths:
        full = root / p if not Path(p).is_absolute() else Path(p)
        try:
            result[p] = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            result[p] = ""
    return result


def _any_file_contains(contents: dict[str, str], pattern: str) -> bool:
    rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    return any(rx.search(text) for text in contents.values() if text)


def _files_exist(root: Path, paths: list[str]) -> list[str]:
    return [p for p in paths if (root / p if not Path(p).is_absolute() else Path(p)).exists()]


# ---------------------------------------------------------------------------
# Language-specific matchers
# ---------------------------------------------------------------------------

def _python_has_protocol(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\bclass\b.*\(Protocol\)") or \
           _any_file_contains(contents, r"\bclass\b.*\(ABC\)")


def _python_has_context(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\bclass\b.*Context")


def _python_has_concrete(contents: dict[str, str]) -> bool:
    count = sum(
        1 for text in contents.values()
        if re.search(r"\bclass\b\s+\w+Strategy\b|\bdef execute\b", text or "", re.IGNORECASE)
    )
    return count >= 2  # at least 2 files with execute / Strategy


def _java_has_interface(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\binterface\b\s+\w+Strategy\b|\binterface\b\s+Strategy\b")


def _java_has_context(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\bclass\b.*Context")


def _java_has_concrete(contents: dict[str, str]) -> bool:
    count = sum(
        1 for text in contents.values()
        if re.search(r"\bimplements\b.*Strategy\b", text or "", re.IGNORECASE)
    )
    return count >= 2


def _ts_has_interface(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\binterface\b\s+\w+Strategy\b")


def _ts_has_context(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\bclass\b.*Context")


def _ts_has_concrete(contents: dict[str, str]) -> bool:
    return _any_file_contains(contents, r"\bimplements\b.*Strategy\b")


def _has_test_file(contents: dict[str, str]) -> bool:
    return any(
        "test" in p.lower() and text.strip()
        for p, text in contents.items()
    )


# ---------------------------------------------------------------------------
# Strategy gate (Java / Python / TypeScript)
# ---------------------------------------------------------------------------

def _strategy_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    checks: list[CheckDetail] = []
    existing = _files_exist(root, output_files)
    all_contents = _read_files(root, output_files)

    # --- files exist ---
    missing = [f for f in output_files if f not in existing]
    checks.append(CheckDetail(
        name="files_exist",
        passed=not missing,
        message=f"all {len(output_files)} output files present" if not missing
                else f"missing: {missing}",
        remediation="Re-run the renderer or inspect template path mapping." if missing else "",
    ))

    lang = language.lower()
    if lang == "python":
        checks.append(CheckDetail(
            name="has_protocol_or_abc",
            passed=_python_has_protocol(all_contents),
            message="Protocol/ABC base found" if _python_has_protocol(all_contents) else "No Protocol/ABC found",
            remediation="Add a Protocol or ABC base class for the strategy interface.",
        ))
        checks.append(CheckDetail(
            name="has_context_class",
            passed=_python_has_context(all_contents),
            message="Context class found" if _python_has_context(all_contents) else "No Context class",
            remediation="Add a Context class that holds a reference to the strategy.",
        ))
        checks.append(CheckDetail(
            name="has_concrete_strategies",
            passed=_python_has_concrete(all_contents),
            message="≥2 concrete strategies found" if _python_has_concrete(all_contents) else "<2 concrete strategies",
            remediation="Add at least two concrete strategy implementations.",
        ))
    elif lang == "java":
        checks.append(CheckDetail(
            name="has_strategy_interface",
            passed=_java_has_interface(all_contents),
            message="Strategy interface found" if _java_has_interface(all_contents) else "No Strategy interface",
            remediation="Add a Java interface named <Name>Strategy.",
        ))
        checks.append(CheckDetail(
            name="has_context_class",
            passed=_java_has_context(all_contents),
            message="Context class found" if _java_has_context(all_contents) else "No Context class",
            remediation="Add a Context class that holds a Strategy reference.",
        ))
        checks.append(CheckDetail(
            name="has_concrete_strategies",
            passed=_java_has_concrete(all_contents),
            message="≥2 concrete strategies found" if _java_has_concrete(all_contents) else "<2 concrete strategies",
            remediation="Add at least two classes that implement the Strategy interface.",
        ))
    elif lang in ("typescript", "ts"):
        checks.append(CheckDetail(
            name="has_strategy_interface",
            passed=_ts_has_interface(all_contents),
            message="Strategy interface found" if _ts_has_interface(all_contents) else "No Strategy interface",
            remediation="Add a TypeScript interface <Name>Strategy.",
        ))
        checks.append(CheckDetail(
            name="has_context_class",
            passed=_ts_has_context(all_contents),
            message="Context class found" if _ts_has_context(all_contents) else "No Context class",
            remediation="Add a Context class that holds a strategy reference.",
        ))
        checks.append(CheckDetail(
            name="has_concrete_strategies",
            passed=_ts_has_concrete(all_contents),
            message="Concrete strategy found" if _ts_has_concrete(all_contents) else "No class implements Strategy",
            remediation="Add at least one class implementing the Strategy interface.",
        ))

    if require_tests:
        has_test = _has_test_file(all_contents)
        checks.append(CheckDetail(
            name="has_test_file",
            passed=has_test,
            message="Test file present" if has_test else "No test file found",
            remediation="Add a test file (test_*.py, *Test.java, *.test.ts).",
        ))

    passed_names = [c.name for c in checks if c.passed]
    failed_names = [c.name for c in checks if not c.passed]
    overall = not failed_names
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="strategy",
        language=language,
        passed=overall,
        checked_files=list(output_files),
        passed_checks=passed_names,
        failed_checks=failed_names,
        details=checks,
        remediation_hint=remediation,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_GATE_REGISTRY: dict[str, Callable] = {
    "strategy": _strategy_gate,
    "java.strategy": _strategy_gate,
    "python.strategy": _strategy_gate,
    "ts.strategy": _strategy_gate,
}


class PatternGateService:
    """Runs structural checks for a rendered pattern output."""

    def check(
        self,
        *,
        pattern_id: str,
        language: str,
        output_files: list[str],
        workspace_root: Optional[Path] = None,
        require_tests: bool = True,
    ) -> GateResult:
        """Run structural gate for the given pattern.

        Args:
            pattern_id: the catalog pattern id (e.g. ``python.strategy``).
            language: ``python`` / ``java`` / ``typescript``.
            output_files: list of file paths relative to ``workspace_root``.
            workspace_root: directory that contains the generated files.
                            Defaults to ``Path(".")``.
            require_tests: fail when no test file is found.
        """
        root = workspace_root or Path(".")
        checker = _GATE_REGISTRY.get(pattern_id.lower())
        if checker is None:
            # Generic fallback: only check that files exist
            existing = _files_exist(root, output_files)
            missing = [f for f in output_files if f not in existing]
            detail = CheckDetail(
                name="files_exist",
                passed=not missing,
                message=f"all {len(output_files)} output files present" if not missing
                        else f"missing: {missing}",
                remediation="Re-run the renderer." if missing else "",
            )
            return GateResult(
                pattern_id=pattern_id,
                language=language,
                passed=not missing,
                checked_files=list(output_files),
                passed_checks=["files_exist"] if not missing else [],
                failed_checks=[] if not missing else ["files_exist"],
                details=[detail],
                remediation_hint=detail.remediation,
            )
        return checker(
            root,
            output_files,
            language=language,
            require_tests=require_tests,
        )


_default_gate_service: Optional[PatternGateService] = None


def get_pattern_gate_service() -> PatternGateService:
    global _default_gate_service
    if _default_gate_service is None:
        _default_gate_service = PatternGateService()
    return _default_gate_service
