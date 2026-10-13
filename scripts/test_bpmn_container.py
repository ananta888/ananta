#!/usr/bin/env python3
"""Run the isolated BPMNX-014 core gate without loading the repository .env."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


def repository_identity(root, *, include_browser=False):
    """Actual HEAD plus immutable digest of the test's mounted source families."""
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True, timeout=10,
                              capture_output=True, text=True).stdout.strip()
    if len(revision) != 40 or any(char not in '0123456789abcdef' for char in revision):
        raise ValueError('bpmn_repository_revision_invalid')
    families = ['agent', 'worker', 'ananta_contracts', 'schemas',
                'config/workflow_runtime', 'config/security', 'tests/bpmn_container']
    if include_browser:
        families.extend(['frontend-angular/src', 'frontend-angular/tsconfig.json'])
    names = subprocess.run(['rg', '--files', *families],
                           cwd=root, check=True, timeout=10, capture_output=True, text=True).stdout.splitlines()
    if not 1 <= len(names) <= 20000:
        raise ValueError('bpmn_source_snapshot_limit')
    digest, total = hashlib.sha256(), 0
    for name in sorted(names):
        path = root / name
        if (not name.startswith('frontend-angular/')
                and path.suffix not in {'.py', '.json', '.yaml', '.yml', '.mjs', '.md'}):
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('bpmn_source_snapshot_path_denied')
        content = path.read_bytes()
        total += len(content)
        if total > 100 * 1024 * 1024:
            raise ValueError('bpmn_source_snapshot_size_limit')
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    return revision, digest.hexdigest()


def require_unchanged_repository(root, expected, *, include_browser=False):
    if repository_identity(root, include_browser=include_browser) != expected:
        raise ValueError('bpmn_acceptance_source_changed_during_execution')


def require_cached_images(options, env):
    images = [options.image]
    if options.browser:
        images.append(options.browser_image)
    if options.postgres:
        images.append(options.postgres_image)
    for image in images:
        subprocess.run(['docker', 'image', 'inspect', image], env=env,
                       stdout=subprocess.DEVNULL, check=True, timeout=15)
    if options.browser:
        browser_image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', options.browser_image],
            env=env, check=True, timeout=15, capture_output=True, text=True).stdout.strip()
        if re.fullmatch(r'sha256:[0-9a-f]{64}', browser_image) is None:
            raise ValueError('bpmn_browser_image_identity_invalid')
        env.update(BPMN_BROWSER_IMAGE=browser_image, BPMN_BROWSER_IMAGE_ID=browser_image)


def validate_owned_database_options(options, parser):
    if not options.postgres:
        return
    native_mode = any((options.full_app_autopilot, options.full_app_worker_loss,
                      options.full_app_worker_redelivery, options.full_app_expired_retry,
                      options.full_app_tool_isolation))
    if not native_mode or re.fullmatch(r'sha256:[a-f0-9]{64}', options.postgres_image) is None:
        parser.error('--postgres requires a full Native application mode and an exact cached PostgreSQL image digest')


def configure_owned_database(options, env, command):
    if not options.postgres:
        return
    env.update(BPMN_POSTGRES_ENABLED='1', BPMN_POSTGRES_IMAGE=options.postgres_image, BPMN_HUB_DATABASE_URL=(
        'postgresql+psycopg2://ananta_fixture:synthetic-postgres-fixture-password@postgres:5432/ananta_bpmn_fixture'))
    command.extend(['--profile', 'postgres'])


def start_owned_database(options, command, env):
    if options.postgres:
        subprocess.run(command + ['up', '--detach', '--no-build', '--pull', 'never', '--wait',
                                  '--wait-timeout', '30', 'postgres'], env=env, check=True, timeout=45)


class WorkerRecreation:
    """Host-only fault lifecycle, restricted to one fresh Compose project."""

    def __init__(self, command, env, *, enabled):
        self.command, self.env, self.enabled = command, env, enabled
        self.exit_observed = self.recreated = self.requested = False
        self.cancelled = threading.Event()

    def observe(self, service):
        identity = subprocess.run(self.command + ["ps", "--all", "--quiet", service], env=self.env,
                                  check=True, timeout=10, capture_output=True, text=True).stdout.strip()
        if not identity or "\n" in identity:
            raise ValueError("worker_loss_container_identity_missing")
        state = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", identity],
                               env=self.env, check=True, timeout=10, capture_output=True, text=True)
        return identity, json.loads(state.stdout)

    def handle(self, supplied):
        if (not self.enabled or self.requested or self.cancelled.is_set()
                or not secrets.compare_digest(supplied.encode(), self.env["BPMN_RESTART_TOKEN"].encode())):
            return
        self.requested = True
        try:
            old_id, state = self.observe("autopilot-worker")
            if state.get("Status") != "exited" or state.get("ExitCode") != 73:
                raise ValueError("worker_redelivery_exit_not_observed")
            self.exit_observed = True
            if self.cancelled.is_set():
                return
            subprocess.run(self.command + ["up", "--detach", "--no-build", "--pull", "never", "--no-deps",
                                           "--force-recreate", "autopilot-worker"],
                           env=self.env, check=True, timeout=30)
            new_id, state = self.observe("autopilot-worker")
            self.recreated = old_id != new_id and state.get("Status") == "running"
        except (OSError, ValueError, subprocess.SubprocessError):
            print("Isolated Worker recreation failed", file=sys.stderr)


def consume_output(stream, acceptance, recreation):
    for line in stream:
        if "BPMN_WORKER_RESTART_REQUEST=" in line:
            recreation.handle(line.split("BPMN_WORKER_RESTART_REQUEST=", 1)[1].strip())
        elif "BPMN_CONTAINER_REPORT=" in line:
            try:
                acceptance.update(json.loads(line.split("BPMN_CONTAINER_REPORT=", 1)[1]))
            except (json.JSONDecodeError, TypeError, ValueError):
                print("Invalid BPMN acceptance report", file=sys.stderr)
        else:
            print(line, end="", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-app", action="store_true",
                        help="Run full application bootstrap in a separate container")
    parser.add_argument("--full-app-autopilot", action="store_true",
                        help="Run a full Hub application with real Native Autopilot dispatch and an isolated Worker")
    parser.add_argument("--full-app-worker-loss", action="store_true",
                        help="Terminate the isolated Worker before its result response and verify Hub containment")
    parser.add_argument("--full-app-worker-redelivery", action="store_true",
                        help="Recreate the isolated Worker and admit its persisted Native result")
    parser.add_argument("--full-app-expired-retry", action="store_true",
                        help="Deny expired result admission and exercise an exact Hub-preauthorized fresh attempt")
    parser.add_argument("--full-app-tool-isolation", action="store_true",
                        help="Run real bounded shell tools against the Worker's private result-store boundary")
    parser.add_argument("--image", default="ananta-backend-tests:local")
    parser.add_argument('--postgres', action='store_true', help='Use an owned isolated PostgreSQL Hub database')
    parser.add_argument('--postgres-image',
        default='sha256:5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59')
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
    validate_owned_database_options(options, parser)
    recreation_mode = options.full_app_worker_redelivery or options.full_app_expired_retry
    native_app = (options.full_app_autopilot or options.full_app_worker_loss
                  or recreation_mode or options.full_app_tool_isolation)
    if sum((options.full_app, options.full_app_autopilot, options.full_app_worker_loss,
            options.full_app_worker_redelivery, options.full_app_expired_retry, options.full_app_tool_isolation)) > 1:
        parser.error("Choose one full application mode")
    if (options.full_app or native_app) and (options.browser or options.case):
        parser.error("Full application modes cannot be combined with --browser or --case")
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
        BPMN_WORKER_LOSS_ENABLED="1" if options.full_app_worker_loss or recreation_mode else "0",
        BPMN_WORKER_REDELIVERY_ENABLED="1" if recreation_mode else "0",
        BPMN_NATIVE_RETRY_ENABLED="1" if options.full_app_expired_retry else "0",
        BPMN_TOOL_ISOLATION_ENABLED="1" if options.full_app_tool_isolation else "0",
        BPMN_RESTART_TOKEN=secrets.token_urlsafe(48),
    )
    if native_app or options.browser:
        revision, source_digest = repository_identity(root, include_browser=options.browser)
        env.update(BPMN_REPOSITORY_REVISION=revision, BPMN_REPOSITORY_SOURCE_DIGEST=source_digest)
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
    if options.full_app:
        command += ["--profile", "full-app"]
    if native_app:
        command += ["--profile", "full-app-autopilot"]
    configure_owned_database(options, env, command)
    application_service = "fullapp-autopilot" if native_app else "fullapp"
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
    worker_exit_observed = False
    recreation = WorkerRecreation(command, env, enabled=recreation_mode)
    reader = None

    def read_output(stream):
        consume_output(stream, acceptance, recreation)

    try:
        require_cached_images(options, env)
        start_owned_database(options, command, env)
        print(f"Isolated Compose project: {project}", flush=True)
        if recreation_mode:
            subprocess.run(command + ["run", "--rm", "--no-deps", "-T", "result-volume-init"],
                           env=env, check=True, timeout=30)
        if options.full_app_worker_loss or recreation_mode:
            subprocess.run(command + ["up", "--detach", "--no-build", "--pull", "never", application_service],
                           env=env, check=True, timeout=60)
            execution_command = command + ["logs", "--follow", "--no-color", application_service]
        else:
            execution_command = command + ["up", "--no-build", "--pull", "never", "--abort-on-container-exit",
                                           "--exit-code-from",
                                           application_service if options.full_app or native_app else "hub"]
            execution_command += [application_service] if options.full_app or native_app else []
        process = subprocess.Popen(
            execution_command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        reader = threading.Thread(target=read_output, args=(process.stdout,), daemon=True)
        reader.start()
        code = process.wait(timeout=options.timeout)
        reader.join(timeout=5)
        if native_app or options.browser:
            require_unchanged_repository(root, (revision, source_digest), include_browser=options.browser)
        if options.full_app_worker_loss:
            observed = {}
            for service in (application_service, "autopilot-worker"):
                container = subprocess.run(command + ["ps", "--all", "--quiet", service], env=env,
                                           check=True, timeout=10, capture_output=True, text=True).stdout.strip()
                if not container or "\n" in container:
                    raise ValueError("worker_loss_container_identity_missing")
                state = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", container],
                                       env=env, check=True, timeout=10, capture_output=True, text=True)
                observed[service] = json.loads(state.stdout)
            worker_exit_observed = (observed["autopilot-worker"].get("Status") == "exited"
                                    and observed["autopilot-worker"].get("ExitCode") == 73)
            code = 0 if (worker_exit_observed and observed[application_service].get("Status") == "exited"
                         and observed[application_service].get("ExitCode") == 0) else 1
        if recreation_mode:
            _, hub_state = recreation.observe(application_service)
            code = 0 if (recreation.exit_observed and recreation.recreated and hub_state.get("Status") == "exited"
                         and hub_state.get("ExitCode") == 0) else 1
        if not code and acceptance.get("passed") is not True:
            code = 1
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 130
        print(f"BPMN container acceptance stopped: {type(exc).__name__}", file=sys.stderr)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        code = code or 1
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
        recreation.cancelled.set()
        if reader is not None:
            reader.join(timeout=35)
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
            volumes = subprocess.run(
                ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                env=env, timeout=10, check=True, capture_output=True, text=True,
            )
            cleanup_ok = (cleanup.returncode == 0 and not containers.stdout.strip()
                          and not networks.stdout.strip() and not volumes.stdout.strip())
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
            "full_application_requested": options.full_app or native_app,
            "autopilot_requested": native_app,
            "worker_loss_requested": options.full_app_worker_loss,
            "worker_redelivery_requested": recreation_mode,
            "expired_retry_requested": options.full_app_expired_retry,
            "tool_isolation_requested": options.full_app_tool_isolation,
            'hub_database_backend': 'postgresql' if options.postgres else 'sqlite',
            "worker_container_recreated": recreation.recreated,
            "worker_container_exit_observed": worker_exit_observed or recreation.exit_observed,
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
