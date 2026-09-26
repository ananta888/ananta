#!/usr/bin/env python3
"""Run the isolated BPMNX-014 core gate without loading the repository .env."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="ananta-backend-tests:local")
    parser.add_argument(
        "--browser", action="store_true", help="Include authenticated Chromium editor against the test Hub"
    )
    parser.add_argument("--browser-image", default="ananta-frontend-tests:local")
    parser.add_argument(
        "--case", action="append", default=[], help="Run only a named case (repeatable); partial coverage is reported"
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--report", type=Path, help="New JSON report path; defaults to a private /tmp file")
    options = parser.parse_args()
    if not 30 <= options.timeout <= 600:
        parser.error("--timeout must be between 30 and 600 seconds")
    root = Path(__file__).resolve().parents[1]
    project = "bpmn-container-" + uuid.uuid4().hex[:12]
    if options.report:
        report_path = options.report.resolve()
        report_output = report_path.open("x", encoding="utf-8")
    else:
        report_output = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="bpmn-container-", suffix=".json", delete=False
        )
        report_path = Path(report_output.name)
    # Never inherit Compose profiles, service URLs, credentials or .env values.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "XDG_RUNTIME_DIR",
        }
    }
    env.update(
        BPMN_TEST_ROOT=str(root),
        BPMN_TEST_IMAGE=options.image,
        BPMN_WORKER_TOKEN=secrets.token_urlsafe(48),
        BPMN_DISPATCH_TOKEN=secrets.token_urlsafe(48),
        BPMN_BROWSER_TOKEN=secrets.token_urlsafe(48),
        BPMN_BROWSER_ENABLED="1" if options.browser else "0",
        BPMN_BROWSER_IMAGE=options.browser_image,
        BPMN_CASES=json.dumps(options.case),
    )
    command = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        project,
        "-f",
        str(root / "docker/bpmn-test/compose.yml"),
    ]
    if options.browser:
        command += ["--profile", "browser"]
    started = time.monotonic()
    interrupted = False

    def interrupt(_signum, _frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, interrupt)
    process = None
    code = 1
    cleanup_ok = False
    acceptance = {}
    reader = None

    def read_output(stream):
        for line in stream:
            if "BPMN_CONTAINER_REPORT=" in line:
                try:
                    acceptance.update(json.loads(line.split("BPMN_CONTAINER_REPORT=", 1)[1]))
                except (json.JSONDecodeError, TypeError, ValueError):
                    print("Invalid BPMN acceptance report", file=sys.stderr)
            else:
                print(line, end="", flush=True)

    try:
        subprocess.run(
            ["docker", "image", "inspect", options.image], env=env, stdout=subprocess.DEVNULL, check=True, timeout=15
        )
        if options.browser:
            subprocess.run(
                ["docker", "image", "inspect", options.browser_image],
                env=env,
                stdout=subprocess.DEVNULL,
                check=True,
                timeout=15,
            )
        print(f"Isolated Compose project: {project}", flush=True)
        process = subprocess.Popen(
            command + ["up", "--no-build", "--pull", "never", "--abort-on-container-exit", "--exit-code-from", "hub"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        reader = threading.Thread(target=read_output, args=(process.stdout,), daemon=True)
        reader.start()
        code = process.wait(timeout=options.timeout)
        reader.join(timeout=5)
        if not code and acceptance.get("passed") is not True:
            code = 1
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 130
        print(f"BPMN container acceptance stopped: {type(exc).__name__}", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"BPMN container acceptance unavailable: {exc}", file=sys.stderr)
    finally:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"Compose client termination: {exc}", file=sys.stderr)
        if code and not acceptance:
            try:
                logs = subprocess.run(
                    command + ["logs", "--no-color", "--tail", "100"],
                    env=env,
                    timeout=10,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                read_output(logs.stdout.splitlines(keepends=True))
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"Diagnostics unavailable: {exc}", file=sys.stderr)
        try:
            cleanup = subprocess.run(
                command + ["down", "--volumes", "--timeout", "5"], env=env, timeout=30, check=False
            )
            containers = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                env=env,
                timeout=10,
                check=True,
                capture_output=True,
                text=True,
            )
            networks = subprocess.run(
                ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                env=env,
                timeout=10,
                check=True,
                capture_output=True,
                text=True,
            )
            cleanup_ok = cleanup.returncode == 0 and not containers.stdout.strip() and not networks.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Cleanup failed for {project}: {exc}", file=sys.stderr)
        if reader is not None:
            reader.join(timeout=5)
        code = code or (0 if cleanup_ok else 1)
        summary = {
            "schema": "bpmn_container_runner.v1",
            "project": project,
            "exit_code": code,
            "cleanup_complete": cleanup_ok,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "evidence_classification": "synthetic_test_technical_observation",
            "production_release_evidence": False,
            "partial_case_selection": bool(options.case),
            "browser_requested": options.browser,
            "cases": {name: value.get("passed", False) for name, value in acceptance.get("cases", {}).items()},
        }
        json.dump({**summary, "acceptance": acceptance}, report_output, indent=2, sort_keys=True)
        report_output.write("\n")
        report_output.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
        print(json.dumps({**summary, "report": str(report_path)}), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
