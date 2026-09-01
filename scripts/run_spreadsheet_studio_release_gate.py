#!/usr/bin/env python3
"""Run closed, automatic Spreadsheet Studio release gates with honest not_run states."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "config/release-gates/spreadsheet-studio.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/spreadsheet-studio-release.json"
PROFILE_SCHEMA = ROOT / "schemas/spreadsheet-studio/release-profile.v1.json"
RESULT_SCHEMA = ROOT / "schemas/spreadsheet-studio/release-result.v1.json"
_GATE_IDS = (
    "unit_contract_property",
    "security_negative",
    "libreoffice_real_file",
    "container_recovery",
    "angular_accessibility_e2e",
    "gpu_lora_smoke",
)
_ENVIRONMENTS = frozenset({"cpu", "libreoffice", "docker", "browser", "optional_nvidia"})
_EVIDENCE_KINDS = frozenset({"exit_code", "junit", "playwright_json", "json_status"})
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+")


class SpreadsheetReleaseGateError(ValueError):
    pass


def load_profile(path: Path) -> dict[str, Any]:
    payload = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    _validate_schema(payload, PROFILE_SCHEMA, "spreadsheet_release_profile_contract_invalid")
    if set(payload) != {"schema", "profile_id", "test_matrix", "input_paths", "gates"}:
        raise SpreadsheetReleaseGateError("spreadsheet_release_profile_fields_invalid")
    if payload["schema"] != "ananta.spreadsheet-studio-release-profile.v1":
        raise SpreadsheetReleaseGateError("spreadsheet_release_profile_schema_invalid")
    if not _SAFE_ID.fullmatch(str(payload["profile_id"])):
        raise SpreadsheetReleaseGateError("spreadsheet_release_profile_id_invalid")
    paths = payload["input_paths"]
    if not isinstance(paths, list) or not paths or payload["test_matrix"] not in paths:
        raise SpreadsheetReleaseGateError("spreadsheet_release_inputs_invalid")
    for value in paths:
        _resolve_relative(value, require_file=True)
    gates = payload["gates"]
    if not isinstance(gates, list) or tuple(gate.get("id") for gate in gates if isinstance(gate, Mapping)) != _GATE_IDS:
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_set_invalid")
    for gate in gates:
        _validate_gate(gate)
    matrix = json.loads(_resolve_relative(payload["test_matrix"], require_file=True).read_text(encoding="utf-8"))
    expected = [(item["name"], item["environment"], item["required"]) for item in matrix.get("gates", [])]
    actual = [(item["id"], item["environment"], item["required"]) for item in gates]
    if actual != expected or matrix.get("human_in_loop_test_requirement") != "forbidden":
        raise SpreadsheetReleaseGateError("spreadsheet_release_test_matrix_mismatch")
    return payload


def _validate_gate(gate: Mapping[str, Any]) -> None:
    allowed = {"id", "environment", "required", "timeout_seconds", "required_environment", "commands"}
    if set(gate) - allowed or not _SAFE_ID.fullmatch(str(gate.get("id") or "")):
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_fields_invalid")
    if gate.get("environment") not in _ENVIRONMENTS or not isinstance(gate.get("required"), bool):
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_environment_invalid")
    timeout = gate.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 30 <= timeout <= 7_200:
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_timeout_invalid")
    required_environment = gate.get("required_environment", [])
    if not isinstance(required_environment, list) or any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", value) for value in required_environment
    ):
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_required_environment_invalid")
    commands = gate.get("commands")
    if not isinstance(commands, list) or not commands:
        raise SpreadsheetReleaseGateError("spreadsheet_release_gate_commands_invalid")
    for command in commands:
        if not isinstance(command, Mapping) or set(command) - {"cwd", "argv", "environment", "evidence_kind"}:
            raise SpreadsheetReleaseGateError("spreadsheet_release_command_fields_invalid")
        _resolve_relative(command.get("cwd"), require_file=False)
        argv = command.get("argv")
        environment = command.get("environment", {})
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(value, str) or not value or "\x00" in value for value in argv)
            or command.get("evidence_kind") not in _EVIDENCE_KINDS
            or not isinstance(environment, Mapping)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items())
        ):
            raise SpreadsheetReleaseGateError("spreadsheet_release_command_invalid")


def environment_status(
    gate: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    probe: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str]:
    values = os.environ if environ is None else environ
    environment = gate["environment"]
    if environment == "cpu":
        return True, "spreadsheet_release_environment_available"
    executable = {"libreoffice": "libreoffice", "docker": "docker", "browser": "node", "optional_nvidia": "nvidia-smi"}[
        environment
    ]
    if which(executable) is None and not (environment == "libreoffice" and which("soffice")):
        return False, f"spreadsheet_{environment}_unavailable"
    missing = [name for name in gate.get("required_environment", []) if not str(values.get(name) or "").strip()]
    if environment == "optional_nvidia" and str(values.get("ANANTA_RUN_SPREADSHEET_GPU_GATE") or "") != "1":
        missing.append("ANANTA_RUN_SPREADSHEET_GPU_GATE")
    if missing:
        return False, "spreadsheet_required_environment_missing"
    if environment == "docker":
        result = probe(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            return False, "spreadsheet_docker_daemon_unavailable"
    if environment == "browser":
        browser_root = Path(values.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache/ms-playwright")
        if not any(browser_root.glob("chromium_headless_shell-*")) and not any(browser_root.glob("chromium-*")):
            return False, "spreadsheet_browser_binary_unavailable"
    if environment == "optional_nvidia":
        result = probe(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False, "spreadsheet_nvidia_device_unavailable"
    return True, "spreadsheet_release_environment_available"


def run_profile(
    profile: Mapping[str, Any],
    *,
    selected: set[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    selected_ids = selected or set(_GATE_IDS)
    unknown = selected_ids - set(_GATE_IDS)
    if unknown:
        raise SpreadsheetReleaseGateError("spreadsheet_release_selection_invalid")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ananta-spreadsheet-release-") as temporary:
        temporary_path = Path(temporary)
        for gate in profile["gates"]:
            if gate["id"] not in selected_ids:
                continue
            available, reason = environment_status(gate, environ=environ)
            if not available:
                results.append(_not_run(gate, reason))
                continue
            results.append(_run_gate(gate, temporary_path=temporary_path, environ=environ))
    required_failed = any(row["required"] and row["status"] != "passed" for row in results)
    report = {
        "schema": "ananta.spreadsheet-studio-release-result.v1",
        "profile_id": profile["profile_id"],
        "git_sha": _git_sha(),
        "input_digests": [
            {
                "path": path,
                "sha256": hashlib.sha256(_resolve_relative(path, require_file=True).read_bytes()).hexdigest(),
            }
            for path in profile["input_paths"]
        ],
        "gates": results,
        "status": "failed" if required_failed else "passed",
        "human_intervention_required": False,
    }
    _validate_schema(report, RESULT_SCHEMA, "spreadsheet_release_result_contract_invalid")
    return report


def _run_gate(gate: Mapping[str, Any], *, temporary_path: Path, environ: Mapping[str, str] | None) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    for index, definition in enumerate(gate["commands"]):
        junit = temporary_path / f"{gate['id']}-{index}.xml"
        playwright_json = temporary_path / f"{gate['id']}-{index}-playwright.json"
        json_result = temporary_path / f"{gate['id']}-{index}.json"
        replacements = {
            "{python}": sys.executable,
            "{gate_tmp}": str(temporary_path),
            "{junit}": str(junit),
            "{playwright_json}": str(playwright_json),
            "{json_result}": str(json_result),
        }
        argv = [_replace(value, replacements) for value in definition["argv"]]
        command_environment = dict(os.environ if environ is None else environ)
        command_environment["PYTHONPATH"] = str(ROOT)
        command_environment.update(
            {key: _replace(value, replacements) for key, value in definition.get("environment", {}).items()}
        )
        if gate["environment"] == "browser":
            command_environment.setdefault("E2E_PORT", str(_available_loopback_port()))
        try:
            completed = subprocess.run(
                argv,
                cwd=_resolve_relative(definition["cwd"], require_file=False),
                env=command_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=int(gate["timeout_seconds"]),
            )
            result = {
                "argv": definition["argv"],
                "evidence_kind": definition["evidence_kind"],
                "returncode": completed.returncode,
            }
            result.update(_evidence(definition["evidence_kind"], junit, playwright_json, json_result))
            if completed.returncode != 0 or not result.get("evidence_valid", True):
                result["stdout_tail"] = _safe_tail(completed.stdout)
                result["stderr_tail"] = _safe_tail(completed.stderr)
        except subprocess.TimeoutExpired:
            result = {
                "argv": definition["argv"],
                "evidence_kind": definition["evidence_kind"],
                "returncode": -1,
                "reason_code": "spreadsheet_release_command_timeout",
            }
        commands.append(result)
    passed = all(command["returncode"] == 0 and command.get("evidence_valid", True) for command in commands)
    return {
        "id": gate["id"],
        "environment": gate["environment"],
        "required": gate["required"],
        "status": "passed" if passed else "failed",
        "reason_code": "spreadsheet_release_gate_passed" if passed else "spreadsheet_release_gate_failed",
        "commands": commands,
    }


def _evidence(kind: str, junit: Path, playwright_json: Path, json_result: Path) -> dict[str, Any]:
    if kind == "exit_code":
        return {"evidence_valid": True}
    try:
        if kind == "junit":
            root = ET.parse(junit).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            counts = {
                field: sum(int(item.attrib.get(field, "0")) for item in suites)
                for field in ("tests", "failures", "errors", "skipped")
            }
            return {
                "evidence_valid": counts["tests"] > 0
                and counts["failures"] == counts["errors"] == counts["skipped"] == 0,
                "counts": counts,
            }
        payload = json.loads(
            (playwright_json if kind == "playwright_json" else json_result).read_text(encoding="utf-8")
        )
        if kind == "playwright_json":
            stats = dict(payload.get("stats") or {})
            valid = (
                int(stats.get("expected") or 0) > 0
                and int(stats.get("unexpected") or 0) == 0
                and int(stats.get("skipped") or 0) == 0
            )
            return {
                "evidence_valid": valid,
                "counts": {key: int(stats.get(key) or 0) for key in ("expected", "unexpected", "flaky", "skipped")},
            }
        return {
            "evidence_valid": payload.get("ok") is True
            and payload.get("nvidia_live_smoke", {}).get("status") == "passed",
            "result_sha256": hashlib.sha256(json_result.read_bytes()).hexdigest(),
        }
    except (ET.ParseError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"evidence_valid": False, "reason_code": "spreadsheet_release_evidence_invalid"}


def _not_run(gate: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": gate["id"],
        "environment": gate["environment"],
        "required": gate["required"],
        "status": "not_run",
        "reason_code": reason,
        "commands": [],
    }


def _replace(value: str, replacements: Mapping[str, str]) -> str:
    result = value
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def _safe_tail(value: str) -> str:
    tail = value[-4_000:]
    return _BEARER.sub(r"\1[REDACTED]", _JWT.sub("[REDACTED_JWT]", tail))


def _resolve_relative(value: object, *, require_file: bool) -> Path:
    text = str(value or "")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise SpreadsheetReleaseGateError("spreadsheet_release_path_invalid")
    resolved = (ROOT / path).resolve()
    if require_file and not resolved.is_file():
        raise SpreadsheetReleaseGateError("spreadsheet_release_input_missing")
    if not require_file and not resolved.is_dir():
        raise SpreadsheetReleaseGateError("spreadsheet_release_cwd_missing")
    return resolved


def _validate_schema(payload: object, path: Path, reason: str) -> None:
    definition = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(definition)
    if next(Draft202012Validator(definition).iter_errors(payload), None) is not None:
        raise SpreadsheetReleaseGateError(reason)


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--group", action="append", choices=[*_GATE_IDS, "all"], default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    selected = set(_GATE_IDS) if not arguments.group or "all" in arguments.group else set(arguments.group)
    try:
        report = run_profile(load_profile(arguments.profile), selected=selected)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    output = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
