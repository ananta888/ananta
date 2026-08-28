#!/usr/bin/env python3
"""Build, smoke and scan optional training images without human input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCANNER_CONFIG = ROOT / "config/security/training-backend-scanners.v1.json"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNRESOLVED_LICENSES = frozenset({"", "none", "noassertion", "unknown"})


class ImageGateError(RuntimeError):
    """Raised when an input or external-tool result violates the gate contract."""


class CommandPort(Protocol):
    def run(self, argv: Sequence[str], *, timeout_seconds: int) -> str: ...


class SubprocessCommandRunner:
    def run(self, argv: Sequence[str], *, timeout_seconds: int) -> str:
        completed = subprocess.run(
            list(argv),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if completed.returncode != 0:
            command = Path(str(argv[0])).name
            raise ImageGateError(f"{command}_exit_{completed.returncode}")
        return completed.stdout


@dataclass(frozen=True, slots=True)
class BackendImage:
    backend: str
    version: str
    package: str
    image: str
    dockerfile: Path
    lockfile: Path


BACKENDS = (
    BackendImage(
        "axolotl",
        "0.18.0",
        "axolotl",
        "ananta-training-axolotl:0.18.0-local",
        ROOT / "docker/compose-next/Dockerfile.training-axolotl",
        ROOT / "docker/compose-next/requirements.training-axolotl.lock.txt",
    ),
    BackendImage(
        "llamafactory",
        "0.9.5",
        "llamafactory",
        "ananta-training-llamafactory:0.9.5-local",
        ROOT / "docker/compose-next/Dockerfile.training-llamafactory",
        ROOT / "docker/compose-next/requirements.training-llamafactory.lock.txt",
    ),
    BackendImage(
        "autotrain",
        "0.8.36",
        "autotrain-advanced",
        "ananta-training-autotrain:0.8.36-local",
        ROOT / "docker/compose-next/Dockerfile.training-autotrain",
        ROOT / "docker/compose-next/requirements.training-autotrain.lock.txt",
    ),
    BackendImage(
        "torchtune",
        "0.6.1",
        "torchtune",
        "ananta-training-torchtune:0.6.1-local",
        ROOT / "docker/compose-next/Dockerfile.training-torchtune",
        ROOT / "docker/compose-next/requirements.training-torchtune.lock.txt",
    ),
)


def load_scanner_config(path: Path = SCANNER_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "syft", "grype", "policy"}:
        raise ImageGateError("scanner_config_contract_invalid")
    if value["schema_version"] != "ananta.training-backend-scanners.v1":
        raise ImageGateError("scanner_config_schema_invalid")
    for scanner in ("syft", "grype"):
        item = value[scanner]
        if not isinstance(item, dict) or set(item) != {"version", "image"}:
            raise ImageGateError("scanner_config_contract_invalid")
        if not isinstance(item["version"], str) or not item["version"]:
            raise ImageGateError("scanner_version_invalid")
        image = str(item["image"])
        if "@sha256:" not in image or not _DIGEST.fullmatch("sha256:" + image.rsplit("@sha256:", 1)[1]):
            raise ImageGateError("scanner_image_not_digest_pinned")
    policy = value["policy"]
    expected = {"maximum_critical", "maximum_high", "maximum_unresolved_licenses"}
    if not isinstance(policy, dict) or set(policy) != expected:
        raise ImageGateError("scanner_policy_contract_invalid")
    if any(not isinstance(policy[key], int) or isinstance(policy[key], bool) or policy[key] < 0 for key in expected):
        raise ImageGateError("scanner_policy_value_invalid")
    return value


class DockerImageGate:
    """Infrastructure adapter; policy evaluation remains pure and testable."""

    def __init__(self, runner: CommandPort, *, timeout_seconds: int = 1800) -> None:
        self._runner = runner
        self._timeout = timeout_seconds

    def build(self, spec: BackendImage) -> None:
        self._runner.run(
            (
                "docker",
                "build",
                "--progress=plain",
                "--file",
                str(spec.dockerfile.relative_to(ROOT)),
                "--tag",
                spec.image,
                ".",
            ),
            timeout_seconds=self._timeout,
        )

    def inspect_and_smoke(self, spec: BackendImage) -> dict[str, Any]:
        raw = self._runner.run(("docker", "image", "inspect", spec.image), timeout_seconds=60)
        try:
            documents = json.loads(raw)
            document = documents[0]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise ImageGateError("container_inspect_invalid") from exc
        digest = document.get("Id")
        size = document.get("Size")
        config = document.get("Config")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ImageGateError("container_digest_invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ImageGateError("container_size_invalid")
        if not isinstance(config, Mapping) or config.get("User") != "10005:10005":
            raise ImageGateError("container_non_root_invalid")
        environment = {
            item.partition("=")[0]: item.partition("=")[2]
            for item in config.get("Env", ())
            if isinstance(item, str) and "=" in item
        }
        for name in ("HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            if environment.get(name) != "1":
                raise ImageGateError("container_offline_environment_invalid")
        prefix = (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--memory",
            "12g",
            "--cpus",
            "4",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--entrypoint",
            "/opt/trainer/bin/python",
            spec.image,
        )
        self._runner.run((*prefix, "-m", "pip", "check"), timeout_seconds=300)
        assertion = (
            "import importlib.metadata as m;"
            f"assert m.version({spec.package!r}) == {spec.version!r}"
        )
        self._runner.run((*prefix, "-c", assertion), timeout_seconds=120)
        return {"container_digest": digest, "image_size_bytes": size, "install_smoke": "verified"}

    def scan(
        self,
        spec: BackendImage,
        scanners: Mapping[str, Any],
        report_root: Path,
        cache_root: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        sbom_path = report_root / f"{spec.backend}.sbom.json"
        grype_path = report_root / f"{spec.backend}.grype.json"
        scanner_tmp = report_root / ".scanner-tmp"
        scanner_tmp.mkdir(exist_ok=True)
        scanner_user = f"{os.getuid()}:{os.getgid()}"
        try:
            docker_socket_group = str(Path("/var/run/docker.sock").stat().st_gid)
        except OSError as exc:
            raise ImageGateError("docker_socket_unavailable") from exc
        self._runner.run(
            (
                "docker",
                "run",
                "--rm",
                "--user",
                scanner_user,
                "--group-add",
                docker_socket_group,
                "--env",
                "HOME=/tmp",
                "--env",
                "SYFT_CHECK_FOR_APP_UPDATE=false",
                "--volume",
                f"{scanner_tmp}:/tmp",
                "--volume",
                "/var/run/docker.sock:/var/run/docker.sock:ro",
                "--volume",
                f"{report_root}:/reports",
                str(scanners["syft"]["image"]),
                "scan",
                f"docker:{spec.image}",
                "--output",
                f"syft-json=/reports/{sbom_path.name}",
            ),
            timeout_seconds=self._timeout,
        )
        self._runner.run(
            (
                "docker",
                "run",
                "--rm",
                "--user",
                scanner_user,
                "--env",
                "HOME=/tmp",
                "--volume",
                f"{scanner_tmp}:/tmp",
                "--env",
                "GRYPE_DB_CACHE_DIR=/cache",
                "--volume",
                f"{cache_root}:/cache",
                "--volume",
                f"{report_root}:/reports",
                str(scanners["grype"]["image"]),
                f"sbom:/reports/{sbom_path.name}",
                "--output",
                "json",
                "--file",
                f"/reports/{grype_path.name}",
            ),
            timeout_seconds=self._timeout,
        )
        return _read_document(sbom_path), _read_document(grype_path)


def evaluate_scan(sbom: Mapping[str, Any], scanner: Mapping[str, Any], policy: Mapping[str, int]) -> dict[str, Any]:
    artifacts = sbom.get("artifacts")
    matches = scanner.get("matches")
    if not isinstance(artifacts, list) or not artifacts or not isinstance(matches, list):
        raise ImageGateError("scanner_report_contract_invalid")
    unresolved = 0
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ImageGateError("sbom_artifact_invalid")
        licenses = artifact.get("licenses")
        values: list[str] = []
        if isinstance(licenses, list):
            for license_item in licenses:
                if isinstance(license_item, str):
                    values.append(license_item)
                elif isinstance(license_item, Mapping):
                    values.append(str(license_item.get("spdxExpression") or license_item.get("value") or ""))
        if not any(value.strip().casefold() not in _UNRESOLVED_LICENSES for value in values):
            unresolved += 1
    severities = {name: 0 for name in ("critical", "high", "medium", "low", "negligible", "unknown")}
    for match in matches:
        if not isinstance(match, Mapping) or not isinstance(match.get("vulnerability"), Mapping):
            raise ImageGateError("vulnerability_finding_invalid")
        severity = str(match["vulnerability"].get("severity") or "unknown").casefold()
        severities[severity if severity in severities else "unknown"] += 1
    reasons = []
    if severities["critical"] > policy["maximum_critical"]:
        reasons.append("critical_vulnerability")
    if severities["high"] > policy["maximum_high"]:
        reasons.append("high_vulnerability")
    if unresolved > policy["maximum_unresolved_licenses"]:
        reasons.append("unresolved_dependency_license")
    return {
        "status": "passed" if not reasons else "failed",
        "reason_codes": reasons,
        "package_count": len(artifacts),
        "unresolved_license_count": unresolved,
        "vulnerabilities": severities,
    }


def source_sha256(spec: BackendImage, scanner_config: Path = SCANNER_CONFIG) -> str:
    digest = hashlib.sha256()
    for path in (spec.dockerfile, spec.lockfile, scanner_config):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def run_gate(*, build: bool, scan: bool, runner: CommandPort, timeout_seconds: int) -> dict[str, Any]:
    scanners = load_scanner_config()
    docker = DockerImageGate(runner, timeout_seconds=timeout_seconds)
    results: list[dict[str, Any]] = []
    with (
        tempfile.TemporaryDirectory(prefix="ananta-training-scan-") as report_dir,
        tempfile.TemporaryDirectory(prefix="ananta-grype-cache-") as cache_dir,
    ):
        report_root = Path(report_dir)
        cache_root = Path(cache_dir)
        for spec in BACKENDS:
            if build:
                docker.build(spec)
            result = {
                "backend": spec.backend,
                "backend_version": spec.version,
                "image": spec.image,
                "source_sha256": source_sha256(spec),
                **docker.inspect_and_smoke(spec),
            }
            if scan:
                sbom, vulnerability_report = docker.scan(spec, scanners, report_root, cache_root)
                result.update(evaluate_scan(sbom, vulnerability_report, scanners["policy"]))
            else:
                result.update({"status": "blocked", "reason_codes": ["supply_chain_scan_not_run"]})
            results.append(result)
    status = "passed" if all(result["status"] == "passed" for result in results) else "failed"
    return {
        "schema_version": "ananta.training-backend-image-gate.v1",
        "status": status,
        "scanners": {
            name: {"version": scanners[name]["version"], "image": scanners[name]["image"]}
            for name in ("syft", "grype")
        },
        "results": results,
    }


def _read_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageGateError("scanner_report_invalid") from exc
    if not isinstance(value, dict):
        raise ImageGateError("scanner_report_invalid")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="Build all four digest-pinned images first.")
    parser.add_argument("--scan", action="store_true", help="Run pinned Syft and Grype scanners.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    try:
        report = run_gate(
            build=args.build,
            scan=args.scan,
            runner=SubprocessCommandRunner(),
            timeout_seconds=args.timeout_seconds,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (ImageGateError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
