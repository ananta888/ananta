from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta doctor",
        description="Run local, repeatable setup checks for Ananta CLI.",
    )
    parser.add_argument("--json", action="store_true", help="Print checks as JSON.")
    return parser


def run_checks(cwd: Path, env: Mapping[str, str]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    config_path = cwd / "config.json"
    if config_path.exists():
        checks.append(
            {
                "id": "config-json",
                "status": "ok",
                "summary": f"Found {config_path.name}.",
                "next_step": "",
            }
        )
    else:
        checks.append(
            {
                "id": "config-json",
                "status": "fail",
                "summary": "Missing config.json.",
                "next_step": (
                    "Run `ananta init --yes --runtime-mode local-dev --llm-backend ollama "
                    "--model ananta-default --apply-config`."
                ),
            }
        )

    profile_path = cwd / "ananta.runtime-profile.json"
    if profile_path.exists():
        checks.append(
            {
                "id": "runtime-profile",
                "status": "ok",
                "summary": f"Found {profile_path.name}.",
                "next_step": "",
            }
        )
    else:
        checks.append(
            {
                "id": "runtime-profile",
                "status": "warn",
                "summary": "Missing ananta.runtime-profile.json.",
                "next_step": "Run `ananta init` to generate a runtime profile.",
            }
        )

    base_url = env.get("ANANTA_BASE_URL", "http://localhost:5000")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
        checks.append(
            {
                "id": "base-url",
                "status": "ok",
                "summary": f"ANANTA_BASE_URL resolves to {base_url}.",
                "next_step": "",
            }
        )
    else:
        checks.append(
            {
                "id": "base-url",
                "status": "fail",
                "summary": f"ANANTA_BASE_URL value is invalid: {base_url!r}.",
                "next_step": "Set a valid URL, e.g. `export ANANTA_BASE_URL=http://localhost:5000`.",
            }
        )

    if shutil.which("docker"):
        checks.append(
            {
                "id": "docker-binary",
                "status": "ok",
                "summary": "Docker binary found in PATH.",
                "next_step": "",
            }
        )
    else:
        checks.append(
            {
                "id": "docker-binary",
                "status": "warn",
                "summary": "Docker binary not found in PATH.",
                "next_step": "Install Docker only if you plan to use compose/podman runtime modes.",
            }
        )

    if sys.version_info >= (3, 10):
        checks.append(
            {
                "id": "python-version",
                "status": "ok",
                "summary": f"Python {sys.version_info.major}.{sys.version_info.minor} detected.",
                "next_step": "",
            }
        )
    else:
        checks.append(
            {
                "id": "python-version",
                "status": "fail",
                "summary": f"Python {sys.version_info.major}.{sys.version_info.minor} is unsupported.",
                "next_step": "Use Python 3.10 or newer.",
            }
        )

    return checks


def _format_status(status: str) -> str:
    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    return labels.get(status, status.upper())


def _render_text(checks: list[dict[str, str]], output_fn) -> None:
    output_fn("Ananta doctor")
    for check in checks:
        output_fn(f"- [{_format_status(check['status'])}] {check['summary']}")
        if check["next_step"]:
            output_fn(f"  Next: {check['next_step']}")


def main(
    argv: list[str] | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    output_fn=print,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    effective_cwd = cwd or Path.cwd()
    effective_env = env or {}
    checks = run_checks(effective_cwd, effective_env)

    failures = sum(1 for check in checks if check["status"] == "fail")
    warnings = sum(1 for check in checks if check["status"] == "warn")

    if args.json:
        payload = {
            "summary": {
                "total_checks": len(checks),
                "failures": failures,
                "warnings": warnings,
            },
            "checks": checks,
        }
        output_fn(json.dumps(payload, indent=2))
    else:
        _render_text(checks, output_fn)
        output_fn(f"Summary: {len(checks)} checks, {failures} failures, {warnings} warnings.")

    return 1 if failures else 0
