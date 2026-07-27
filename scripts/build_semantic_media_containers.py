#!/usr/bin/env python3
"""Build and bind every container covered by the semantic-media release gate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.semantic_media_program_evidence import source_hash  # noqa: E402

OUTPUT = ROOT / "artifacts/domain/semantic-media-container-builds.json"
SFU_DIGEST = "2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
SFU_TAG = "livekit/livekit-server:v1.13.1"
SFU_REFERENCE = f"{SFU_TAG}@sha256:{SFU_DIGEST}"
TURN_DIGEST = "71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888"
TURN_TAG = "coturn/coturn:4.6.3-r3"
TURN_REFERENCE = f"{TURN_TAG}@sha256:{TURN_DIGEST}"
BUILD_COMPONENTS = frozenset({"hub", "frontend", "reconciliation", "training", "sfu", "turn"})
BUILD_SOURCE_DIRECTORIES = (
    "agent",
    "ananta_contracts",
    "voice_runtime",
    "worker",
    "frontend-angular/src",
    "docker/compose-next",
    "services/evolver_bridge",
)
BUILD_SOURCE_FILES = (
    ".dockerignore",
    ".env.example",
    "pyproject.toml",
    "requirements.lock",
    "docker-compose.semantic-media.yml",
    "frontend-angular/Dockerfile",
    "frontend-angular/angular.json",
    "frontend-angular/package.json",
    "frontend-angular/package-lock.json",
    "frontend-angular/tsconfig.json",
    "frontend-angular/tsconfig.app.json",
    "scripts/build_semantic_media_containers.py",
    "scripts/generate_semantic_media_supply_chain_reports.py",
)
_IGNORED_BUILD_PARTS = frozenset(
    {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules", "dist"}
)


class ContainerBuildError(RuntimeError):
    """A bounded build or image-binding check failed."""


@dataclass(frozen=True, slots=True)
class ImageBuild:
    component: str
    tag: str
    command: tuple[str, ...]


BUILDS = (
    ImageBuild(
        "hub",
        "ananta-quickstart-no-ollama:local",
        (
            "docker",
            "build",
            "--file",
            "docker/compose-next/Dockerfile.quickstart-no-ollama",
            "--tag",
            "ananta-quickstart-no-ollama:local",
            ".",
        ),
    ),
    ImageBuild(
        "frontend",
        "ananta-frontend-tests:local",
        (
            "docker",
            "build",
            "--file",
            "frontend-angular/Dockerfile",
            "--tag",
            "ananta-frontend-tests:local",
            ".",
        ),
    ),
    ImageBuild(
        "reconciliation",
        "ananta-speech-reconciliation-worker:local-cpu",
        (
            "docker",
            "build",
            "--file",
            "docker/compose-next/Dockerfile.speech-reconciliation-worker",
            "--target",
            "cpu",
            "--tag",
            "ananta-speech-reconciliation-worker:local-cpu",
            ".",
        ),
    ),
    ImageBuild(
        "training",
        "ananta-speech-training-worker:local",
        (
            "docker",
            "build",
            "--file",
            "docker/compose-next/Dockerfile.speech-training-worker",
            "--tag",
            "ananta-speech-training-worker:local",
            ".",
        ),
    ),
)


def _run(command: Sequence[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        executable = Path(command[0]).name if command else "unknown"
        raise ContainerBuildError(f"container_build_command_failed:{executable}:{completed.returncode}")
    return completed


def image_digest(reference: str, *, timeout_seconds: int = 60) -> str:
    rendered = (
        _run(
            ("docker", "image", "inspect", "--format", "{{.Id}}", reference),
            timeout_seconds=timeout_seconds,
        )
        .stdout.strip()
        .removeprefix("sha256:")
    )
    if not re.fullmatch(r"[a-f0-9]{64}", rendered):
        raise ContainerBuildError("container_image_digest_invalid")
    return rendered


def ensure_pinned_image(*, tag: str, reference: str, expected_digest: str, timeout_seconds: int) -> str:
    """Materialize one external image and fail closed on tag/digest drift."""

    try:
        digest = image_digest(tag)
    except ContainerBuildError:
        _run(("docker", "pull", reference), timeout_seconds=timeout_seconds)
        _run(("docker", "tag", reference, tag), timeout_seconds=60)
        digest = image_digest(tag)
    if digest != expected_digest:
        raise ContainerBuildError("container_external_digest_mismatch")
    return digest


def build_all(*, timeout_seconds: int) -> dict[str, object]:
    images: list[dict[str, str]] = []
    for build in BUILDS:
        _run(build.command, timeout_seconds=timeout_seconds)
        images.append(
            {
                "component": build.component,
                "image_digest": image_digest(build.tag),
                "reference": build.tag,
            }
        )
    images.append(
        {
            "component": "sfu",
            "image_digest": ensure_pinned_image(
                tag=SFU_TAG,
                reference=SFU_REFERENCE,
                expected_digest=SFU_DIGEST,
                timeout_seconds=timeout_seconds,
            ),
            "reference": SFU_REFERENCE,
        }
    )
    images.append(
        {
            "component": "turn",
            "image_digest": ensure_pinned_image(
                tag=TURN_TAG,
                reference=TURN_REFERENCE,
                expected_digest=TURN_DIGEST,
                timeout_seconds=timeout_seconds,
            ),
            "reference": TURN_REFERENCE,
        }
    )
    return {
        "schema": "ananta.semantic-media-container-builds.v2",
        "source_sha256": build_source_sha256(),
        "images": sorted(images, key=lambda row: row["component"]),
        "status": "passed",
    }


def build_source_projection(*, root: Path = ROOT) -> tuple[str, ...]:
    paths = set(BUILD_SOURCE_FILES)
    for relative in BUILD_SOURCE_DIRECTORIES:
        base = root / relative
        if not base.is_dir():
            raise ContainerBuildError("container_build_source_directory_missing")
        paths.update(
            path.relative_to(root).as_posix()
            for path in base.rglob("*")
            if path.is_file() and not (_IGNORED_BUILD_PARTS & set(path.parts))
        )
    if any(not (root / relative).is_file() for relative in paths):
        raise ContainerBuildError("container_build_source_missing")
    return tuple(sorted(paths))


def build_source_sha256(*, root: Path = ROOT) -> str:
    return source_hash(root, build_source_projection(root=root))


def validate_build_manifest(
    value: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> dict[str, tuple[str, str]]:
    if (
        set(value) != {"schema", "source_sha256", "images", "status"}
        or value.get("schema") != "ananta.semantic-media-container-builds.v2"
        or value.get("status") != "passed"
        or value.get("source_sha256") != build_source_sha256(root=root)
    ):
        raise ContainerBuildError("container_build_manifest_invalid_or_stale")
    images = value.get("images")
    if not isinstance(images, list) or len(images) != len(BUILD_COMPONENTS):
        raise ContainerBuildError("container_build_manifest_coverage_invalid")
    resolved: dict[str, tuple[str, str]] = {}
    for row in images:
        if not isinstance(row, Mapping) or set(row) != {"component", "image_digest", "reference"}:
            raise ContainerBuildError("container_build_manifest_image_invalid")
        component = str(row.get("component") or "")
        digest = str(row.get("image_digest") or "")
        reference = str(row.get("reference") or "")
        if (
            component not in BUILD_COMPONENTS
            or component in resolved
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}", reference) is None
        ):
            raise ContainerBuildError("container_build_manifest_image_invalid")
        resolved[component] = (reference, digest)
    if set(resolved) != BUILD_COMPONENTS:
        raise ContainerBuildError("container_build_manifest_coverage_invalid")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 60 <= args.timeout_seconds <= 7200:
        parser.error("--timeout-seconds must be between 60 and 7200")
    try:
        report = build_all(timeout_seconds=args.timeout_seconds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (ContainerBuildError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "components": [row["component"] for row in report["images"]]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
