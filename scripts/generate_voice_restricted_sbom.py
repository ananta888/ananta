#!/usr/bin/env python3
"""Generate and validate SPDX dependency evidence for local runtime images."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Mapping, Sequence

REPORT_SCHEMA = "ananta.voice-restricted-sbom-report.v1"
FORBIDDEN_HUB_PACKAGES = frozenset({"onnxruntime", "sentence-transformers", "torch", "transformers"})
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document"
_MAX_ATTESTATION_JSON_BYTES = 32 * 1024 * 1024
_MAX_ATTESTATION_DOCUMENT_BYTES = 64 * 1024 * 1024
_MAX_ATTESTATION_DOCUMENTS = 256


def summarize_spdx(*, label: str, image: str, image_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not str(payload.get("spdxVersion") or "").startswith("SPDX-2."):
        raise ValueError(f"{label}: docker sbom did not return SPDX 2.x")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise ValueError(f"{label}: SPDX report contains no packages")
    packages: list[dict[str, str]] = []
    for raw in raw_packages:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        packages.append(
            {
                "name": name,
                "version": str(raw.get("versionInfo") or "").strip(),
                "license": str(raw.get("licenseConcluded") or raw.get("licenseDeclared") or "").strip(),
            }
        )
    if not packages:
        raise ValueError(f"{label}: SPDX report contains no named packages")
    packages.sort(key=lambda item: (item["name"].casefold(), item["version"], item["license"]))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "label": label,
        "image": image,
        "image_id": image_id,
        "spdx_sha256": hashlib.sha256(canonical).hexdigest(),
        "package_count": len(packages),
        "packages": packages,
    }


def validate_hub_package_boundary(summary: Mapping[str, Any]) -> None:
    normalized = {
        str(item.get("name") or "").strip().casefold().replace("_", "-")
        for item in summary.get("packages") or ()
        if isinstance(item, Mapping)
    }
    forbidden = normalized & FORBIDDEN_HUB_PACKAGES
    if forbidden:
        raise ValueError(f"Hub image contains restricted ML packages: {sorted(forbidden)}")


def _docker_image_id(image: str) -> str:
    return subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def extract_buildkit_spdx(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    image_id: str,
) -> Mapping[str, Any]:
    """Resolve the SPDX in-toto predicate attached to a local OCI image index."""

    index_digest = str(image_id).removeprefix("sha256:")
    index = documents.get(index_digest)
    if not isinstance(index, Mapping):
        raise ValueError("local image archive does not contain its OCI index")
    attestations = [
        item
        for item in index.get("manifests") or ()
        if isinstance(item, Mapping)
        and (item.get("annotations") or {}).get("vnd.docker.reference.type") == "attestation-manifest"
    ]
    if len(attestations) != 1:
        raise ValueError("local image must contain exactly one BuildKit attestation manifest")
    attestation_descriptor = attestations[0]
    attestation_digest = str(attestation_descriptor.get("digest") or "").removeprefix("sha256:")
    attestation = documents.get(attestation_digest)
    if not isinstance(attestation, Mapping):
        raise ValueError("BuildKit attestation manifest is missing from the local image archive")
    target_digest = str(
        (attestation_descriptor.get("annotations") or {}).get("vnd.docker.reference.digest") or ""
    ).removeprefix("sha256:")
    spdx_layers = [
        item
        for item in attestation.get("layers") or ()
        if isinstance(item, Mapping)
        and (item.get("annotations") or {}).get("in-toto.io/predicate-type") == SPDX_PREDICATE_TYPE
    ]
    if len(spdx_layers) != 1:
        raise ValueError("BuildKit attestation must contain exactly one SPDX predicate")
    statement_digest = str(spdx_layers[0].get("digest") or "").removeprefix("sha256:")
    statement = documents.get(statement_digest)
    if not isinstance(statement, Mapping) or statement.get("predicateType") != SPDX_PREDICATE_TYPE:
        raise ValueError("BuildKit SPDX statement is missing or has the wrong predicate type")
    subjects = statement.get("subject") or ()
    if target_digest and not any(
        isinstance(subject, Mapping)
        and str((subject.get("digest") or {}).get("sha256") or "") == target_digest
        for subject in subjects
    ):
        raise ValueError("BuildKit SPDX statement is not bound to the image manifest")
    predicate = statement.get("predicate")
    if not isinstance(predicate, Mapping):
        raise ValueError("BuildKit SPDX statement has no document predicate")
    return predicate


def _buildkit_attested_sbom(image: str, *, image_id: str) -> Mapping[str, Any]:
    process = subprocess.Popen(
        ["docker", "image", "save", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover - subprocess contract
        process.kill()
        raise RuntimeError("docker image save did not expose bounded pipes")
    documents: dict[str, Mapping[str, Any]] = {}
    document_bytes = 0
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith("blobs/sha256/"):
                    continue
                if member.size <= 0 or member.size > _MAX_ATTESTATION_JSON_BYTES:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(_MAX_ATTESTATION_JSON_BYTES + 1)
                if len(raw) > _MAX_ATTESTATION_JSON_BYTES:
                    raise ValueError("local image attestation JSON exceeds its size limit")
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, Mapping):
                    document_bytes += len(raw)
                    if (
                        document_bytes > _MAX_ATTESTATION_DOCUMENT_BYTES
                        or len(documents) >= _MAX_ATTESTATION_DOCUMENTS
                    ):
                        raise ValueError("local image contains too much attestation metadata")
                    documents[member.name.rsplit("/", 1)[-1]] = payload
    except Exception:
        process.stdout.close()
        process.kill()
        process.wait(timeout=30)
        raise
    process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace")[-2000:]
    return_code = process.wait(timeout=300)
    if return_code:
        raise RuntimeError(f"docker image save failed: {stderr}")
    return extract_buildkit_spdx(documents, image_id=image_id)


def _docker_sbom(image: str) -> tuple[str, Mapping[str, Any]]:
    image_id = _docker_image_id(image)
    try:
        completed = subprocess.run(
            ["docker", "sbom", "--format", "spdx-json", image],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return image_id, _buildkit_attested_sbom(image, image_id=image_id)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, Mapping):
        raise ValueError("docker sbom returned a non-object document")
    return image_id, payload


def _image_pair(value: str) -> tuple[str, str]:
    label, separator, image = str(value).partition("=")
    if not separator or not label.strip() or not image.strip():
        raise argparse.ArgumentTypeError("--image must use label=image")
    return label.strip(), image.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", type=_image_pair, required=True)
    parser.add_argument("--hub-label", default="hub")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    labels = [label for label, _image in arguments.image]
    if len(labels) != len(set(labels)):
        parser.error("image labels must be unique")
    summaries = []
    try:
        for label, image in arguments.image:
            image_id, spdx = _docker_sbom(image)
            summary = summarize_spdx(label=label, image=image, image_id=image_id, payload=spdx)
            if label == arguments.hub_label:
                validate_hub_package_boundary(summary)
            summaries.append(summary)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    report = {
        "schema_version": REPORT_SCHEMA,
        "images": summaries,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"schema_version": REPORT_SCHEMA, "image_count": len(summaries)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
