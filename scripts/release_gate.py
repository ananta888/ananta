from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "Dockerfile",
    "frontend-angular/Dockerfile",
    "docker-compose.base.yml",
    "docker-compose.yml",
    "docker-compose-lite.yml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "requirements-dev.lock",
    "pyproject.toml",
    "frontend-angular/package.json",
    "frontend-angular/package-lock.json",
    "docs/release-dependency-locking.md",
    "docs/release-environment.md",
    "docs/release-checklist.md",
]

RELEASE_IMAGE_FILES = [
    "Dockerfile",
    "Dockerfile.compose-test",
    "Dockerfile.evolver-bridge",
    "Dockerfile.ollama-wsl-amd",
    "frontend-angular/Dockerfile",
    "docker-compose.base.yml",
    "docker-compose.yml",
    "docker-compose-lite.yml",
    "docker-compose.ollama-wsl.yml",
    "docker-compose.dev-vulkan-live.yml",
]

RELEASE_CI_FILE = ".github/workflows/quality-and-docs.yml"

LOCAL_IMAGE_PREFIXES = (
    "ananta-",
    "ollama-wsl-amd:",
)

FLOATING_TAG_PATTERNS = [
    re.compile(r"\blatest\b"),
    re.compile(r"^alpine$"),
    re.compile(r"^\d+$"),
    re.compile(r"^\d+-alpine$"),
    re.compile(r"^\d+-slim$"),
    re.compile(r"^\d+-bookworm$"),
    re.compile(r"^\d+\.\d+-slim$"),
    re.compile(r"^\d+\.\d+-bookworm$"),
]

SEMVER_EXACT = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class CheckResult:
    def __init__(self, name: str, ok: bool, detail: str) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def run_command(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def docker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {**os.environ, **(extra or {})}
    if env.get("ANANTA_DOCKER_CLEAN_PATH") == "1":
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env.setdefault("DOCKER_CONFIG", "/tmp/ananta-docker-config")
    return env


def npm_command() -> list[str]:
    return shlex.split(os.environ.get("ANANTA_NPM_COMMAND", "npm"))


def package_name(spec: str) -> str:
    return re.split(r"\s*(?:==|>=|<=|~=|!=|>|<|\[)", spec, maxsplit=1)[0].strip().lower().replace("_", "-")


def requirement_names(path: str) -> set[str]:
    names: set[str] = set()
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        names.add(package_name(stripped))
    return names


def lock_entries(path: str) -> list[str]:
    entries: list[str] = []
    for line in read_text(path).splitlines():
        if line.startswith((" ", "\t")):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        entries.append(stripped)
    return entries


def check_required_files() -> CheckResult:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    return CheckResult(
        "required-files",
        not missing,
        "all required release files exist" if not missing else f"missing: {', '.join(missing)}",
    )


def check_python_dependency_sources() -> CheckResult:
    pyproject = tomllib.loads(read_text("pyproject.toml"))
    project_deps = {package_name(dep) for dep in pyproject["project"].get("dependencies", [])}
    dev_deps = {
        package_name(dep)
        for dep in pyproject["project"].get("optional-dependencies", {}).get("dev", [])
    }
    runtime = requirement_names("requirements.txt")
    dev = requirement_names("requirements-dev.txt")
    problems = []
    if project_deps != runtime:
        problems.append(f"runtime mismatch: pyproject-only={sorted(project_deps - runtime)}, requirements-only={sorted(runtime - project_deps)}")
    if dev_deps != dev:
        problems.append(f"dev mismatch: pyproject-only={sorted(dev_deps - dev)}, requirements-dev-only={sorted(dev - dev_deps)}")
    overlap = runtime & dev
    if overlap:
        problems.append(f"runtime/dev source overlap: {sorted(overlap)}")
    return CheckResult(
        "python-dependency-sources",
        not problems,
        "runtime and dev dependency sources match pyproject" if not problems else "; ".join(problems),
    )


def check_python_locks() -> CheckResult:
    problems = []
    for path in ("requirements.lock", "requirements-dev.lock"):
        entries = lock_entries(path)
        unpinned = [entry for entry in entries if "==" not in entry]
        if not entries:
            problems.append(f"{path} has no package entries")
        if unpinned:
            problems.append(f"{path} unpinned entries: {unpinned[:10]}")
    return CheckResult(
        "python-locks",
        not problems,
        "python lockfiles contain pinned package entries" if not problems else "; ".join(problems),
    )


def check_frontend_manifest() -> CheckResult:
    package_json = json.loads(read_text("frontend-angular/package.json"))
    package_lock = json.loads(read_text("frontend-angular/package-lock.json"))
    lock_packages = package_lock.get("packages", {})
    problems = []

    for section in ("dependencies", "devDependencies"):
        for name, version in package_json.get(section, {}).items():
            if not SEMVER_EXACT.match(version):
                problems.append(f"{section}.{name} is not exact: {version}")
            locked_version = lock_packages.get(f"node_modules/{name}", {}).get("version")
            if locked_version and locked_version != version:
                problems.append(f"{section}.{name} package={version} lock={locked_version}")

    node_engine = package_json.get("engines", {}).get("node")
    if node_engine != ">=20.19.0":
        problems.append(f"frontend node engine must remain >=20.19.0, got {node_engine!r}")

    return CheckResult(
        "frontend-manifest",
        not problems,
        "frontend package manifest uses exact top-level versions matching package-lock" if not problems else "; ".join(problems),
    )


def image_references(path: str) -> Iterable[tuple[int, str, str]]:
    for index, line in enumerate(read_text(path).splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("FROM "):
            ref = stripped.split()[1]
            yield index, "FROM", ref
        elif stripped.startswith("image:"):
            ref = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            yield index, "image", ref


def image_tag(ref: str) -> str | None:
    if ref.endswith(":local"):
        return "local"
    if "@" in ref:
        return ref.rsplit("@", 1)[1]
    slash_tail = ref.rsplit("/", 1)[-1]
    if ":" not in slash_tail:
        return None
    return slash_tail.rsplit(":", 1)[1]


def is_floating_tag(tag: str | None) -> bool:
    if tag is None:
        return True
    if tag == "local" or tag.startswith("sha256:"):
        return False
    return any(pattern.search(tag) for pattern in FLOATING_TAG_PATTERNS)


def requires_digest(ref: str) -> bool:
    if "${" in ref:
        return False
    if any(ref.startswith(prefix) for prefix in LOCAL_IMAGE_PREFIXES):
        return False
    if ref.endswith(":local"):
        return False
    return True


def check_image_pinning() -> CheckResult:
    problems = []
    for path in RELEASE_IMAGE_FILES:
        for line, kind, ref in image_references(path):
            tag = image_tag(ref)
            if is_floating_tag(tag):
                problems.append(f"{path}:{line} {kind} uses floating image ref {ref}")
            if requires_digest(ref) and "@sha256:" not in ref:
                problems.append(f"{path}:{line} {kind} public image ref is not digest-pinned: {ref}")
    return CheckResult(
        "image-pinning",
        not problems,
        "release Dockerfiles and Compose files use explicit tags plus digests" if not problems else "; ".join(problems),
    )


def check_tool_pinning() -> CheckResult:
    problems = []
    dockerfile = read_text("Dockerfile")
    if "OPENCODE_AI_VERSION=1.14.18" not in dockerfile:
        problems.append("Dockerfile must pin OPENCODE_AI_VERSION=1.14.18")
    if 'opencode-ai@${OPENCODE_AI_VERSION}' not in dockerfile:
        problems.append("Dockerfile must install opencode-ai with the pinned version")
    if "opencode --version | grep -F" not in dockerfile:
        problems.append("Dockerfile must verify the opencode CLI version during build")

    workflow = read_text(RELEASE_CI_FILE)
    if "@mermaid-js/mermaid-cli@11.12.0" not in workflow:
        problems.append("CI must pin @mermaid-js/mermaid-cli@11.12.0")
    if re.search(r"npm (?:i|install) -g @mermaid-js/mermaid-cli(?:\s|$)", workflow):
        problems.append("CI contains unpinned mermaid-cli global install")
    return CheckResult(
        "tool-pinning",
        not problems,
        "global release tools are pinned and version-checked" if not problems else "; ".join(problems),
    )


def check_ci_release_paths() -> CheckResult:
    workflow = read_text(RELEASE_CI_FILE)
    problems = []
    required_snippets = [
        'python-version: "3.11"',
        "pip install -r requirements.lock",
        "pip install -r requirements-dev.lock",
        'node-version: "20.19.5"',
        "npm ci",
        "python scripts/release_gate.py",
    ]
    for snippet in required_snippets:
        if snippet not in workflow:
            problems.append(f"missing CI snippet: {snippet}")
    if "pip install -r requirements.txt" in workflow or "pip install -r requirements-dev.txt" in workflow:
        problems.append("CI must install Python dependencies from lockfiles, not source requirements")
    return CheckResult(
        "ci-release-paths",
        not problems,
        "CI uses locked Python deps, npm ci, pinned Node, and release gate" if not problems else "; ".join(problems),
    )


def check_todo_status() -> CheckResult:
    data = json.loads(read_text("todo.json"))
    items = [item for category in data.get("categories", []) for item in category.get("items", [])]
    statuses = Counter(item.get("status", "open") for item in items)
    meta = data.get("meta", {}).get("by_status", {})
    rel012 = next((item for item in items if item.get("id") == "REL-012"), None)
    problems = []
    if dict(statuses) != {key: value for key, value in meta.items() if value}:
        expected = {key: statuses.get(key, 0) for key in ("completed", "partial", "open")}
        if expected != meta:
            problems.append(f"todo meta mismatch: expected {expected}, got {meta}")
    if rel012 and rel012.get("status") != "completed":
        problems.append("REL-012 must be completed once this release gate is in use")
    return CheckResult(
        "todo-status",
        not problems,
        "todo status counters are synchronized and REL-012 is completed" if not problems else "; ".join(problems),
    )


def check_compose_config() -> CheckResult:
    env = {
        "POSTGRES_PASSWORD": "test-postgres-password",
        "INITIAL_ADMIN_PASSWORD": "test-admin-password",
        "SECRET_KEY": "test-secret-key-with-at-least-thirty-two-chars",
        "AGENT_TOKEN_HUB": "hub-token",
        "AGENT_TOKEN_ALPHA": "alpha-token",
        "AGENT_TOKEN_BETA": "beta-token",
        "AGENT_TOKEN_GAMMA": "gamma-token",
        "AGENT_TOKEN_DELTA": "delta-token",
        "GRAFANA_PASSWORD": "test-grafana-password",
    }
    commands = [
        ["docker", "compose", "-f", "docker-compose.base.yml", "-f", "docker-compose-lite.yml", "config"],
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.base.yml",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.distributed.yml",
            "config",
        ],
    ]
    failures = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=docker_env(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            failures.append(f"{' '.join(command)} failed: {result.stdout[-1000:]}")
    return CheckResult(
        "compose-config",
        not failures,
        "release compose configurations render successfully" if not failures else "; ".join(failures),
    )


def check_frontend_build() -> CheckResult:
    install = subprocess.run(
        [*npm_command(), "ci", "--no-audit", "--no-fund"],
        cwd=ROOT / "frontend-angular",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if install.returncode != 0:
        return CheckResult("frontend-build", False, f"npm ci failed: {install.stdout[-1000:]}")
    build = subprocess.run(
        [*npm_command(), "run", "build"],
        cwd=ROOT / "frontend-angular",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return CheckResult(
        "frontend-build",
        build.returncode == 0,
        "frontend npm ci and build passed" if build.returncode == 0 else f"npm run build failed: {build.stdout[-1000:]}",
    )


def check_image_builds() -> CheckResult:
    commands = [
        ["docker", "build", "-t", "ananta-backend:release-gate", "."],
        ["docker", "build", "-t", "ananta-frontend:release-gate", "frontend-angular"],
    ]
    failures = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=docker_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            failures.append(f"{' '.join(command)} failed: {result.stdout[-1000:]}")
    return CheckResult(
        "image-builds",
        not failures,
        "backend and frontend release images build successfully" if not failures else "; ".join(failures),
    )


def build_report(results: list[CheckResult]) -> dict:
    return {
        "release_target": "v1.0.0",
        "ok": all(result.ok for result in results),
        "checks": [result.to_dict() for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ananta release reproducibility checks.")
    parser.add_argument("--compose-config", action="store_true", help="validate release Docker Compose configs")
    parser.add_argument("--frontend-build", action="store_true", help="run frontend npm ci and build")
    parser.add_argument("--build-images", action="store_true", help="build backend and frontend Docker images")
    parser.add_argument("--report", help="write a JSON verification report to this path")
    args = parser.parse_args()

    results = [
        check_required_files(),
        check_python_dependency_sources(),
        check_python_locks(),
        check_frontend_manifest(),
        check_image_pinning(),
        check_tool_pinning(),
        check_ci_release_paths(),
        check_todo_status(),
    ]
    if args.compose_config:
        results.append(check_compose_config())
    if args.frontend_build:
        results.append(check_frontend_build())
    if args.build_images:
        results.append(check_image_builds())

    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")

    report = build_report(results)
    if args.report:
        report_path = ROOT / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
