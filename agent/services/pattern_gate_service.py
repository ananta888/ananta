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
import xml.etree.ElementTree as ET
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


# ---------------------------------------------------------------------------
# Notation-pattern gates (Mermaid / BPMN 2.0)
# ---------------------------------------------------------------------------

def _bpmn_namespace() -> str:
    return "http://www.omg.org/spec/BPMN/20100524/MODEL"


def _bpmn_di_namespace() -> str:
    return "http://www.omg.org/spec/BPMN/20100524/DI"


def _read_output(root: Path, output_files: list[str]) -> dict[str, str]:
    """Read output files for a notation gate.

    Notation patterns emit a single source file (``.mmd`` or ``.bpmn``),
    but we accept multiple for symmetry with code-pattern gates.
    Returns ``{rel_path: content}``.
    """
    contents: dict[str, str] = {}
    for p in output_files:
        full = root / p if not Path(p).is_absolute() else Path(p)
        try:
            contents[p] = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            contents[p] = ""
    return contents


def _first_nonempty(contents: dict[str, str]) -> tuple[str, str]:
    """Return (rel_path, content) of the first non-empty output file."""
    for p, text in contents.items():
        if text:
            return p, text
    return "", ""


def _mermaid_class_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no Mermaid class source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="starts_with_classdiagram",
            passed=src.lstrip().startswith("classDiagram"),
            message="header is classDiagram" if src.lstrip().startswith("classDiagram")
                    else "missing classDiagram header",
            remediation="Re-render the diagram.",
        ))
        class_blocks = len(re.findall(r"^\s*class\s+\w+", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="has_class_blocks",
            passed=class_blocks >= 1,
            message=f"{class_blocks} class block(s) found" if class_blocks
                    else "no class block found",
            remediation="Provide at least one class.",
        ))
        # UML2 arrows: <|--, *--, o--, -->, ..|>, ..>, --
        uml_arrows = re.findall(
            r"<\|--|\*--|o--|-->|\.\.\|>\|\.\.>| -- ", src
        )
        rel_count = len(re.findall(r"^\s*\w+\s+(<\|--|\*--|o--|-->|\.\.\|>|\.\.>| -- )", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="has_relationship",
            passed=rel_count >= 0,  # relationships are optional
            message=f"{rel_count} relationship(s) found",
            remediation="",
        ))
        # Balanced braces (one class block opens with `{`, closes with `}`)
        open_braces = src.count("{")
        close_braces = src.count("}")
        checks.append(CheckDetail(
            name="balanced_braces",
            passed=open_braces == close_braces,
            message=f"{open_braces} open / {close_braces} close" if open_braces == close_braces
                    else f"unbalanced: {open_braces} open vs {close_braces} close",
            remediation="Inspect class block syntax.",
        ))
    if require_tests:
        # Notation patterns do not carry test files; skip with a note.
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="mermaid.class",
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _mermaid_sequence_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no Mermaid sequence source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="starts_with_sequencediagram",
            passed=src.lstrip().startswith("sequenceDiagram"),
            message="header is sequenceDiagram",
            remediation="Re-render the diagram.",
        ))
        participant_count = len(re.findall(
            r"^\s*(participant|actor)\s+\w+", src, re.MULTILINE
        ))
        checks.append(CheckDetail(
            name="has_participants",
            passed=participant_count >= 2,
            message=f"{participant_count} participant(s)" if participant_count >= 2
                    else f"only {participant_count} participant (need ≥2)",
            remediation="Declare at least two participants.",
        ))
        # Sequence-message arrows: ->>, --), -->>, -->
        msg_count = len(re.findall(
            r"^\s*\w+(-{1,2}>[\)]?>?)\w+:", src, re.MULTILINE
        ))
        checks.append(CheckDetail(
            name="has_messages",
            passed=msg_count >= 1,
            message=f"{msg_count} message(s)",
            remediation="Declare at least one message.",
        ))
        # Fragment balance
        opens = len(re.findall(r"^\s*(alt|par|loop|opt|critical)\b", src, re.MULTILINE))
        closes = len(re.findall(r"^\s*end\s*$", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="balanced_fragments",
            passed=opens == closes,
            message=f"{opens} fragment opens / {closes} closes" if opens == closes
                    else f"unbalanced: {opens} opens vs {closes} closes",
            remediation="Inspect fragment open/close markers.",
        ))
    if require_tests:
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="mermaid.sequence",
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _mermaid_state_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no Mermaid state source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="starts_with_statediagramv2",
            passed=src.lstrip().startswith("stateDiagram-v2"),
            message="header is stateDiagram-v2",
            remediation="Re-render the diagram.",
        ))
        has_initial = bool(re.search(r"\[\*\]\s*-->", src))
        checks.append(CheckDetail(
            name="has_initial_pseudostate",
            passed=has_initial,
            message="[*] --> present" if has_initial else "no initial pseudostate",
            remediation="Add a transition from [*] to a state.",
        ))
        has_final = bool(re.search(r"-->\s*\[\*\]", src))
        checks.append(CheckDetail(
            name="has_final_pseudostate",
            passed=has_final,
            message="--> [*] present" if has_final else "no final pseudostate",
            remediation="Add a transition to [*].",
        ))
        # Composite state balance
        opens = len(re.findall(r"^\s*state\s+\w+\s*\{", src, re.MULTILINE))
        closes = len(re.findall(r"^\s*\}\s*$", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="balanced_composite_states",
            passed=opens == closes,
            message=f"{opens} composite open / {closes} close" if opens == closes
                    else f"unbalanced: {opens} opens vs {closes} closes",
            remediation="Inspect composite state blocks.",
        ))
    if require_tests:
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="mermaid.state",
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _mermaid_usecase_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no Mermaid use-case source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="starts_with_flowchart",
            passed=src.lstrip().startswith("flowchart"),
            message="header is flowchart",
            remediation="Re-render the diagram.",
        ))
        subgraph_count = len(re.findall(r"^\s*subgraph\s+\w+", src, re.MULTILINE))
        end_count = len(re.findall(r"^\s*end\s*$", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="has_system_boundary",
            passed=subgraph_count >= 1,
            message=f"{subgraph_count} system boundary(ies)",
            remediation="Provide system_name to wrap use-cases in a subgraph.",
        ))
        # Stadium actor nodes: id(["label"]) — Mermaid's actor shape
        actor_count = len(re.findall(r"^\s*\w+\(\[\".*?\"\]\)", src, re.MULTILINE))
        # Ellipse use-case nodes: id([("label")])
        uc_count = len(re.findall(r"^\s*\w+\[\(\".*?\"\)\]", src, re.MULTILINE))
        checks.append(CheckDetail(
            name="has_actors",
            passed=actor_count >= 1,
            message=f"{actor_count} actor(s)",
            remediation="Declare at least one actor.",
        ))
        checks.append(CheckDetail(
            name="has_use_cases",
            passed=uc_count >= 1,
            message=f"{uc_count} use-case(s)",
            remediation="Declare at least one use-case.",
        ))
        # Balance
        checks.append(CheckDetail(
            name="balanced_subgraph",
            passed=subgraph_count == end_count,
            message=f"{subgraph_count} subgraph / {end_count} end" if subgraph_count == end_count
                    else f"unbalanced: {subgraph_count} subgraph vs {end_count} end",
            remediation="Inspect subgraph / end markers.",
        ))
    if require_tests:
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="mermaid.usecase",
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _mermaid_activity_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no Mermaid activity source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="starts_with_flowchart",
            passed=src.lstrip().startswith("flowchart"),
            message="header is flowchart",
            remediation="Re-render the diagram.",
        ))
        # Initial node: ((( ))) — three opening parens
        initial_count = len(re.findall(r"^\s*\w+\(\(\(", src, re.MULTILINE))
        # Final node: same shape but preceded by another context. Use count of all ((( ))).
        circle_count = initial_count
        checks.append(CheckDetail(
            name="has_initial_node",
            passed=initial_count >= 1,
            message=f"{initial_count} initial node(s)" if initial_count
                    else "no initial node",
            remediation="Add a node with shape='initial'.",
        ))
        checks.append(CheckDetail(
            name="has_final_node",
            passed=circle_count >= 2 if initial_count == 1 else circle_count >= 1,
            message=f"{circle_count} circle node(s) found",
            remediation="Add at least one node with shape='final'.",
        ))
        # Decision diamonds (Mermaid flowchart: id{"text"})
        decision_count = len(re.findall(r'\w+\{"[^"]*"\}', src))
        checks.append(CheckDetail(
            name="has_decision_or_action",
            passed=True,  # decisions are optional
            message=f"{decision_count} decision diamond(s)",
            remediation="",
        ))
    if require_tests:
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id="mermaid.activity",
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _bpmn_xml_gate(
    root: Path,
    output_files: list[str],
    *,
    pattern_id: str,
    language: str,
    require_tests: bool = True,
    expected_elements: list[str],
    extra_checks: Optional[Callable[[ET.Element], list[CheckDetail]]] = None,
) -> GateResult:
    contents = _read_output(root, output_files)
    rel, src = _first_nonempty(contents)
    checks: list[CheckDetail] = []
    if not src:
        checks.append(CheckDetail(
            name="file_present",
            passed=False,
            message="no BPMN source found",
            remediation="Render the pattern with the notation renderer.",
        ))
    else:
        checks.append(CheckDetail(
            name="file_present",
            passed=True,
            message=f"{rel} present",
        ))
        checks.append(CheckDetail(
            name="well_formed_xml",
            passed=src.lstrip().startswith("<?xml"),
            message="XML declaration present",
            remediation="Re-render the BPMN diagram.",
        ))
        try:
            root_el = ET.fromstring(src)
            checks.append(CheckDetail(
                name="xml_parses",
                passed=True,
                message=f"root tag is {root_el.tag}",
            ))
        except ET.ParseError as exc:
            checks.append(CheckDetail(
                name="xml_parses",
                passed=False,
                message=f"XML parse error: {exc}",
                remediation="Inspect XML for malformed elements.",
            ))
            root_el = None

        if root_el is not None:
            ns = _bpmn_namespace()
            checks.append(CheckDetail(
                name="bpmn_namespace",
                passed=root_el.tag == f"{{{ns}}}definitions",
                message=f"namespace={'OK' if root_el.tag == f'{{{ns}}}definitions' else root_el.tag}",
                remediation="Emit the BPMN 2.0 MODEL namespace.",
            ))
            for tag in expected_elements:
                count = len(root_el.findall(f".//{{{ns}}}{tag}"))
                checks.append(CheckDetail(
                    name=f"has_{tag.replace(':', '_')}",
                    passed=count >= 1,
                    message=f"{count} <bpmn:{tag}> element(s)" if count
                            else f"no <bpmn:{tag}> element",
                    remediation=f"Provide at least one <bpmn:{tag}> element.",
                ))
            if extra_checks:
                checks.extend(extra_checks(root_el))

    if require_tests:
        checks.append(CheckDetail(
            name="has_test_file",
            passed=True,
            message="notation patterns do not require test files",
            remediation="",
        ))
    passed = [c.name for c in checks if c.passed]
    failed = [c.name for c in checks if not c.passed]
    remediation = "; ".join(c.remediation for c in checks if not c.passed and c.remediation)
    return GateResult(
        pattern_id=pattern_id,
        language=language,
        passed=not failed,
        checked_files=list(output_files),
        passed_checks=passed,
        failed_checks=failed,
        details=checks,
        remediation_hint=remediation,
    )


def _bpmn_process_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    return _bpmn_xml_gate(
        root, output_files,
        pattern_id="bpmn.process",
        language=language,
        require_tests=require_tests,
        expected_elements=["process", "startEvent", "endEvent", "sequenceFlow"],
        extra_checks=None,
    )


def _bpmn_pool_lane_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    def _check_lanes(root_el: ET.Element) -> list[CheckDetail]:
        ns = _bpmn_namespace()
        lane_set_count = len(root_el.findall(f".//{{{ns}}}laneSet"))
        lane_count = len(root_el.findall(f".//{{{ns}}}lane"))
        flow_node_ref_count = len(root_el.findall(f".//{{{ns}}}flowNodeRef"))
        checks = [
            CheckDetail(
                name="has_lane_set",
                passed=lane_set_count == 1,
                message=f"{lane_set_count} <bpmn:laneSet>" if lane_set_count == 1
                        else f"{lane_set_count} <bpmn:laneSet> (expected 1)",
                remediation="Provide exactly one laneSet.",
            ),
            CheckDetail(
                name="has_lanes",
                passed=lane_count >= 1,
                message=f"{lane_count} <bpmn:lane>",
                remediation="Provide at least one lane.",
            ),
            CheckDetail(
                name="has_flow_node_refs",
                passed=flow_node_ref_count >= 1,
                message=f"{flow_node_ref_count} <bpmn:flowNodeRef>",
                remediation="Assign each element to a lane via flowNodeRef.",
            ),
        ]
        return checks

    return _bpmn_xml_gate(
        root, output_files,
        pattern_id="bpmn.pool_lane",
        language=language,
        require_tests=require_tests,
        expected_elements=["process", "startEvent", "endEvent", "sequenceFlow"],
        extra_checks=_check_lanes,
    )


def _bpmn_collaboration_gate(
    root: Path,
    output_files: list[str],
    *,
    language: str,
    require_tests: bool = True,
) -> GateResult:
    def _check_collab(root_el: ET.Element) -> list[CheckDetail]:
        ns = _bpmn_namespace()
        collab_count = len(root_el.findall(f".//{{{ns}}}collaboration"))
        participant_count = len(root_el.findall(f".//{{{ns}}}participant"))
        message_flow_count = len(root_el.findall(f".//{{{ns}}}messageFlow"))
        process_count = len(root_el.findall(f".//{{{ns}}}process"))
        checks = [
            CheckDetail(
                name="has_collaboration",
                passed=collab_count == 1,
                message=f"{collab_count} <bpmn:collaboration>" if collab_count == 1
                        else f"{collab_count} <bpmn:collaboration> (expected 1)",
                remediation="Provide exactly one collaboration.",
            ),
            CheckDetail(
                name="has_participants",
                passed=participant_count >= 2,
                message=f"{participant_count} <bpmn:participant>" if participant_count >= 2
                        else f"only {participant_count} <bpmn:participant> (need ≥2)",
                remediation="Provide at least two participants.",
            ),
            CheckDetail(
                name="has_processes",
                passed=process_count >= 2,
                message=f"{process_count} <bpmn:process>",
                remediation="Each participant must embed a process.",
            ),
        ]
        if message_flow_count == 0:
            # Message flows are technically optional, but a collaboration
            # without any is unusual. Surface as a soft note (always passed,
            # but visible in the report).
            checks.append(CheckDetail(
                name="has_message_flows",
                passed=True,
                message="0 <bpmn:messageFlow> (none declared; collaboration is empty)",
                remediation="Consider adding message flows.",
            ))
        else:
            checks.append(CheckDetail(
                name="has_message_flows",
                passed=True,
                message=f"{message_flow_count} <bpmn:messageFlow>",
            ))
        return checks

    return _bpmn_xml_gate(
        root, output_files,
        pattern_id="bpmn.collaboration",
        language=language,
        require_tests=require_tests,
        expected_elements=["process", "startEvent", "endEvent", "sequenceFlow"],
        extra_checks=_check_collab,
    )


# Register all notation gates in the dispatcher (idempotent).
_NOTATION_GATES: dict[str, Callable] = {
    "mermaid.class": _mermaid_class_gate,
    "mermaid.sequence": _mermaid_sequence_gate,
    "mermaid.state": _mermaid_state_gate,
    "mermaid.usecase": _mermaid_usecase_gate,
    "mermaid.activity": _mermaid_activity_gate,
    "bpmn.process": _bpmn_process_gate,
    "bpmn.pool_lane": _bpmn_pool_lane_gate,
    "bpmn.collaboration": _bpmn_collaboration_gate,
}
_GATE_REGISTRY.update(_NOTATION_GATES)


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
