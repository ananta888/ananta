"""POST /api/deterministic/run — execute a deterministic step directly.

Subtypes supported:
  script      — run shell command via bash -c
  api-call    — HTTP request via curl (url in command)
  regex-check — run command, check stdout against pattern
  file-check  — check file/dir exists and optionally matches pattern
  python      — run a python3 one-liner

Safety policy:
  - Hard-blocked: rm -rf, dd, mkfs, :(){ → fork bombs, curl|bash pipelines
  - Timeout: default 10s, max 30s
  - Working directory: /project-workspaces (safe sandbox)
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from flask import Blueprint, request, jsonify

det_run_bp = Blueprint("det_run", __name__, url_prefix="/api/deterministic")

_BLOCKED_PATTERNS = [
    r"rm\s+-[rf]",
    r"\bdd\b.*of=",
    r"\bmkfs\b",
    r":\(\)\{",           # fork bomb
    r">\s*/dev/sd",
    r"chmod\s+000",
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"\bsudo\b",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_PATTERNS]

_SAFE_WORKDIR = "/project-workspaces"


def _is_blocked(cmd: str) -> str | None:
    for pattern in _BLOCKED_RE:
        if pattern.search(cmd):
            return f"Blocked pattern: {pattern.pattern}"
    return None


def _run_shell(cmd: str, timeout: int) -> dict:
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout,
            cwd=_SAFE_WORKDIR,
        )
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[:4096],
            "stderr": result.stderr[:2048],
            "duration_ms": duration,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "Timeout expired", "duration_ms": timeout * 1000, "success": False}
    except Exception as exc:
        return {"exit_code": -1, "stdout": "", "stderr": str(exc), "duration_ms": 0, "success": False}


@det_run_bp.post("/run")
def run_det_step():
    body = request.get_json(silent=True) or {}
    subtype: str = str(body.get("subtype") or "script").strip().lower()
    command: str = str(body.get("command") or "").strip()
    expected: str = str(body.get("expected_result") or "").strip()
    timeout: int = min(int(body.get("timeout") or 10), 30)

    if not command:
        return jsonify({"error": "command_required"}), 400

    # Safety gate
    blocked = _is_blocked(command)
    if blocked:
        return jsonify({"error": "command_blocked", "reason": blocked, "success": False}), 403

    if subtype == "script":
        result = _run_shell(command, timeout)

    elif subtype == "api-call":
        # command is the URL; build safe curl invocation
        if not command.startswith("http"):
            return jsonify({"error": "api-call requires http/https URL"}), 400
        curl_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time {timeout} {shlex.quote(command)}"
        result = _run_shell(curl_cmd, timeout + 2)
        http_code = result.get("stdout", "").strip()
        result["http_code"] = http_code
        if expected and http_code:
            result["success"] = http_code == expected.strip()

    elif subtype == "regex-check":
        # command is a shell command; expected is the regex pattern
        result = _run_shell(command, timeout)
        if expected and result.get("stdout"):
            matched = bool(re.search(expected, result["stdout"], re.MULTILINE))
            result["regex_match"] = matched
            result["success"] = matched

    elif subtype == "file-check":
        # command is the file/dir path; expected is optional regex on content
        path = command
        check_cmd = f"test -e {shlex.quote(path)} && echo EXISTS || echo MISSING"
        result = _run_shell(check_cmd, 5)
        result["path"] = path
        if expected and result.get("stdout", "").strip() == "EXISTS":
            grep_cmd = f"grep -c {shlex.quote(expected)} {shlex.quote(path)} 2>/dev/null"
            grep_result = _run_shell(grep_cmd, 5)
            result["pattern_match"] = grep_result["exit_code"] == 0
            result["success"] = grep_result["exit_code"] == 0
        else:
            result["success"] = result.get("stdout", "").strip() == "EXISTS"

    elif subtype == "python":
        py_cmd = f"python3 -c {shlex.quote(command)}"
        result = _run_shell(py_cmd, timeout)

    else:
        return jsonify({"error": f"unknown_subtype: {subtype}"}), 400

    # Check expected result against stdout if not already checked
    if expected and subtype in ("script", "python") and "success" in result:
        if expected.startswith("exit "):
            try:
                expected_code = int(expected.split()[1])
                result["success"] = result["exit_code"] == expected_code
            except (ValueError, IndexError):
                pass
        elif re.search(expected, result.get("stdout", ""), re.IGNORECASE):
            result["success"] = True

    result["subtype"] = subtype
    result["command"] = command
    return jsonify(result), 200


@det_run_bp.get("/subtypes")
def list_subtypes():
    return jsonify([
        {"id": "script",      "label": "Shell-Script / Befehl", "placeholder": "npm test", "expected_hint": "exit 0"},
        {"id": "api-call",    "label": "API-Aufruf (HTTP)",      "placeholder": "https://...", "expected_hint": "200"},
        {"id": "regex-check", "label": "Regex-Prüfung",          "placeholder": "cat /app/app.log", "expected_hint": "^OK"},
        {"id": "file-check",  "label": "Datei-Check",            "placeholder": "/app/dist/main.js", "expected_hint": ""},
        {"id": "python",      "label": "Python-Ausdruck",        "placeholder": "print(1+1)", "expected_hint": "2"},
    ]), 200
