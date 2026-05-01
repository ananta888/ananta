from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"
DEFAULT_OUT = ROOT / "ci-artifacts" / "eclipse" / "ananta-eclipse-update-site"
DEFAULT_PLUGIN_JAR = PLUGIN_ROOT / "build" / "libs" / "ananta-eclipse-plugin-runtime-0.1.0-bootstrap.jar"
DEFAULT_DOCKER_IMAGE = "ananta/eclipse-ui-e2e:local"
DOCKERFILE = ROOT / "docker" / "eclipse-ui-e2e" / "Dockerfile"
PLUGIN_ID = "io.ananta.eclipse.runtime"
FEATURE_ID = "io.ananta.eclipse.runtime.feature"
VERSION = "0.1.0.qualifier"


def build_update_site(
    *,
    out_dir: Path = DEFAULT_OUT,
    build_plugin: bool = True,
    bundle_path: Path | None = None,
    publish_p2: bool = True,
    docker_image: str = DEFAULT_DOCKER_IMAGE,
    build_docker_image: bool = True,
    publish_category: bool = False,
) -> dict[str, Any]:
    if build_plugin:
        result = subprocess.run(
            [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "build"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {
                "schema": "eclipse_update_site_report_v1",
                "ok": False,
                "reason": "plugin_build_failed",
                "output": (result.stdout + "\n" + result.stderr).strip(),
            }

    plugin_jar = bundle_path or DEFAULT_PLUGIN_JAR
    if not plugin_jar.exists():
        return {
            "schema": "eclipse_update_site_report_v1",
            "ok": False,
            "reason": "plugin_jar_missing",
            "plugin_jar": _relative_or_absolute(plugin_jar),
        }

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = out_dir / "plugins"
    features_dir = out_dir / "features"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    features_dir.mkdir(parents=True, exist_ok=True)

    copied_plugin = plugins_dir / f"{PLUGIN_ID}_{VERSION}.jar"
    shutil.copy2(plugin_jar, copied_plugin)
    feature_jar = features_dir / f"{FEATURE_ID}_{VERSION}.jar"
    _write_feature_jar(feature_jar)
    category_xml = out_dir / "category.xml"
    _write_category_xml(category_xml)

    publisher_report: dict[str, Any] = {"enabled": publish_p2}
    if publish_p2:
        publisher_report = _publish_p2_repository(
            out_dir=out_dir,
            docker_image=docker_image,
            build_docker_image=build_docker_image,
            publish_category=publish_category,
        )
        if not publisher_report.get("ok"):
            return {
                "schema": "eclipse_update_site_report_v1",
                "ok": False,
                "reason": "p2_publish_failed",
                "update_site": _relative_or_absolute(out_dir),
                "publisher": publisher_report,
            }

    p2_files = ["content.jar", "artifacts.jar"] if publish_p2 else []
    site = {
        "schema": "eclipse_update_site_manifest_v1",
        "plugin_id": PLUGIN_ID,
        "feature_id": FEATURE_ID,
        "version": VERSION,
        "installable_via_eclipse_ui": publish_p2,
        "artifacts": [
            _relative_or_absolute(copied_plugin),
            _relative_or_absolute(feature_jar),
            _relative_or_absolute(category_xml),
            *[_relative_or_absolute(out_dir / name) for name in p2_files],
        ],
        "scope": "p2_update_site" if publish_p2 else "p2_layout_without_metadata",
        "install_evidence_required_for_runtime_complete": False,
    }
    (out_dir / "site.json").write_text(json.dumps(site, indent=2) + "\n", encoding="utf-8")
    return {
        "schema": "eclipse_update_site_report_v1",
        "ok": True,
        "update_site": _relative_or_absolute(out_dir),
        "plugin": _relative_or_absolute(copied_plugin),
        "feature": _relative_or_absolute(feature_jar),
        "category": _relative_or_absolute(category_xml),
        "p2_metadata": [_relative_or_absolute(out_dir / name) for name in p2_files],
        "publisher": publisher_report,
    }


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _docker_env() -> dict[str, str]:
    env = dict(os.environ)
    if env.get("ANANTA_DOCKER_CLEAN_PATH") == "1":
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env.setdefault("DOCKER_CONFIG", "/tmp/ananta-docker-config")
    return env


def _write_feature_jar(feature_jar: Path) -> None:
    feature_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feature
      id="{FEATURE_ID}"
      label="Ananta Eclipse Runtime"
      version="{VERSION}"
      provider-name="Ananta">

   <description>
      Ananta Eclipse Runtime client surface for Hub-governed goals, tasks, chat, review and repair workflows.
   </description>

   <plugin
         id="{PLUGIN_ID}"
         download-size="0"
         install-size="0"
         version="{VERSION}"
         unpack="false"/>

</feature>
"""
    with zipfile.ZipFile(feature_jar, "w", compression=zipfile.ZIP_DEFLATED) as feature:
        feature.writestr("feature.xml", feature_xml)


def _write_category_xml(category_xml: Path) -> None:
    category_xml.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<site>
   <feature url="features/{FEATURE_ID}_{VERSION}.jar" id="{FEATURE_ID}" version="{VERSION}">
      <category name="ananta"/>
   </feature>
   <category-def name="ananta" label="Ananta"/>
</site>
""",
        encoding="utf-8",
    )


def _build_docker_image(*, docker_image: str) -> dict[str, Any]:
    if not shutil.which("docker"):
        return {"ok": False, "reason": "docker_missing"}
    result = subprocess.run(
        ["docker", "build", "-f", str(DOCKERFILE), "-t", docker_image, str(DOCKERFILE.parent)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
        env=_docker_env(),
    )
    return {
        "ok": result.returncode == 0,
        "image": docker_image,
        "output_tail": (result.stdout + "\n" + result.stderr).strip()[-4000:],
    }


def _docker_publisher_command(
    *,
    docker_image: str,
    out_dir: Path,
    application: str,
    extra_args: list[str],
    use_xvfb: bool = False,
) -> list[str]:
    relative_out = out_dir.resolve().relative_to(ROOT.resolve()).as_posix()
    repository_uri = f"file:/workspace/{relative_out}"
    entrypoint = "xvfb-run" if use_xvfb else "/opt/eclipse/eclipse"
    eclipse_prefix = ["-a", "/opt/eclipse/eclipse"] if use_xvfb else []
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ROOT}:/workspace",
        "-w",
        "/workspace",
        "--entrypoint",
        entrypoint,
        docker_image,
        *eclipse_prefix,
        "-nosplash",
        "-application",
        application,
        "-metadataRepository",
        repository_uri,
        "-artifactRepository",
        repository_uri,
        *extra_args,
    ]


def _publish_p2_repository(
    *,
    out_dir: Path,
    docker_image: str,
    build_docker_image: bool,
    publish_category: bool,
) -> dict[str, Any]:
    try:
        relative_out = out_dir.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return {"ok": False, "reason": "out_dir_must_be_under_repo_for_docker_p2_publish", "out_dir": str(out_dir)}

    checks: list[dict[str, Any]] = [{"check_id": "docker_available", "ok": bool(shutil.which("docker"))}]
    if not shutil.which("docker"):
        return {"ok": False, "checks": checks}

    if build_docker_image:
        image_report = _build_docker_image(docker_image=docker_image)
        checks.append({"check_id": "docker_image_build", **image_report})
        if not image_report.get("ok"):
            return {"ok": False, "checks": checks}

    publisher = subprocess.run(
        _docker_publisher_command(
            docker_image=docker_image,
            out_dir=out_dir,
            application="org.eclipse.equinox.p2.publisher.FeaturesAndBundlesPublisher",
            extra_args=[
                "-source",
                f"/workspace/{relative_out}",
                "-compress",
                "-publishArtifacts",
            ],
        ),
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=_docker_env(),
    )
    checks.append({
        "check_id": "features_and_bundles_publisher",
        "ok": publisher.returncode == 0,
        "output_tail": (publisher.stdout + "\n" + publisher.stderr).strip()[-4000:],
    })
    if publisher.returncode != 0:
        return {"ok": False, "checks": checks}

    if publish_category:
        category = subprocess.run(
            _docker_publisher_command(
                docker_image=docker_image,
                out_dir=out_dir,
                application="org.eclipse.equinox.p2.publisher.CategoryPublisher",
                extra_args=[
                    "-categoryDefinition",
                    f"/workspace/{relative_out}/category.xml",
                    "-compress",
                ],
                use_xvfb=True,
            ),
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
            env=_docker_env(),
        )
        checks.append({
            "check_id": "category_publisher",
            "ok": category.returncode == 0,
            "output_tail": (category.stdout + "\n" + category.stderr).strip()[-4000:],
        })
    else:
        checks.append({
            "check_id": "category_publisher",
            "ok": True,
            "skipped": True,
            "reason": "optional; Eclipse can install from p2 metadata with 'Group items by category' disabled",
        })

    metadata_ok = (out_dir / "content.jar").exists() and (out_dir / "artifacts.jar").exists()
    checks.append({
        "check_id": "p2_metadata_present",
        "ok": metadata_ok,
        "files": [_relative_or_absolute(out_dir / "content.jar"), _relative_or_absolute(out_dir / "artifacts.jar")],
    })
    return {"ok": all(bool(item.get("ok")) for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Eclipse p2 update site for the runtime plugin.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--skip-plugin-build", action="store_true")
    parser.add_argument("--skip-p2-publish", action="store_true")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--skip-docker-build", action="store_true")
    parser.add_argument("--publish-category", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    report = build_update_site(
        out_dir=out_dir,
        build_plugin=not args.skip_plugin_build,
        publish_p2=not args.skip_p2_publish,
        docker_image=args.docker_image,
        build_docker_image=not args.skip_docker_build,
        publish_category=args.publish_category,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
