from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"
DEFAULT_GRADLE_IMAGE = "gradle:8.10.2-jdk17"
REQUIRED_FILES = [
    "settings.gradle",
    "build.gradle",
    "plugin.xml",
    "build.properties",
    "META-INF/MANIFEST.MF",
]


def _missing_required_files(project_dir: Path) -> list[str]:
    missing: list[str] = []
    for relative_path in REQUIRED_FILES:
        if not (project_dir / relative_path).exists():
            missing.append(relative_path)
    return missing


def _docker_build_command(project_dir: Path, *, gradle_image: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ROOT}:/workspace",
        "-w",
        f"/workspace/{project_dir.relative_to(ROOT).as_posix()}",
        gradle_image,
        "gradle",
        "--no-daemon",
        "clean",
        "build",
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate Eclipse runtime plugin bootstrap project.")
    parser.add_argument("--mode", choices=["validate", "build"], default="validate")
    parser.add_argument("--project-dir", default=str(PROJECT_DIR))
    parser.add_argument("--gradle-image", default=DEFAULT_GRADLE_IMAGE)
    return parser.parse_args()


def _docker_env() -> dict[str, str]:
    env = dict(os.environ)
    if env.get("ANANTA_DOCKER_CLEAN_PATH") == "1":
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env.setdefault("DOCKER_CONFIG", "/tmp/ananta-docker-config")
    return env


def main() -> int:
    args = _parse_args()
    project_dir = Path(args.project_dir).resolve()
    missing = _missing_required_files(project_dir)
    if missing:
        print("eclipse-runtime-build-invalid")
        print(f"missing_required_files={missing}")
        return 2

    command = _docker_build_command(project_dir, gradle_image=str(args.gradle_image))
    print("eclipse-runtime-build-command")
    print(shlex.join(command))

    if args.mode == "validate":
        print("eclipse-runtime-build-validate-ok")
        return 0

    if not shutil.which("docker"):
        print("eclipse-runtime-build-failed")
        print("docker-not-found")
        return 2

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=_docker_env(),
    )
    if result.returncode != 0:
        print("eclipse-runtime-build-failed")
        print((result.stdout + "\n" + result.stderr).strip())
        return result.returncode

    print("eclipse-runtime-build-ok")
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
