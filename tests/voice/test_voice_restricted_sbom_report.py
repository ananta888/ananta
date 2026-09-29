from __future__ import annotations

import pytest

from scripts.generate_voice_restricted_sbom import (
    extract_buildkit_spdx,
    summarize_spdx,
    validate_hub_package_boundary,
)


def _spdx(*names: str) -> dict:
    return {
        "spdxVersion": "SPDX-2.3",
        "packages": [
            {
                "name": name,
                "versionInfo": "1.0",
                "licenseConcluded": "Apache-2.0",
            }
            for name in names
        ],
    }


def test_sbom_summary_is_deterministic_and_keeps_dependency_identity() -> None:
    left = summarize_spdx(label="voice", image="voice:v1", image_id="sha256:a", payload=_spdx("flask", "vosk"))
    right = summarize_spdx(label="voice", image="voice:v1", image_id="sha256:a", payload=_spdx("flask", "vosk"))

    assert left == right
    assert left["package_count"] == 2
    assert left["spdx_sha256"]
    assert {item["name"] for item in left["packages"]} == {"flask", "vosk"}


def test_hub_sbom_fails_if_restricted_ml_engine_leaks_into_base_image() -> None:
    safe = summarize_spdx(label="hub", image="hub:v1", image_id="sha256:a", payload=_spdx("flask"))
    validate_hub_package_boundary(safe)

    unsafe = summarize_spdx(
        label="hub",
        image="hub:v1",
        image_id="sha256:b",
        payload=_spdx("flask", "sentence_transformers"),
    )
    with pytest.raises(ValueError, match="restricted ML"):
        validate_hub_package_boundary(unsafe)


@pytest.mark.parametrize("payload", [{}, {"spdxVersion": "SPDX-2.3", "packages": []}])
def test_invalid_or_empty_sbom_fails_closed(payload: dict) -> None:
    with pytest.raises(ValueError):
        summarize_spdx(label="voice", image="voice:v1", image_id="sha256:a", payload=payload)


def test_buildkit_spdx_attestation_is_resolved_and_subject_bound() -> None:
    statement = {
        "predicateType": "https://spdx.dev/Document",
        "subject": [{"name": "image", "digest": {"sha256": "target"}}],
        "predicate": _spdx("vosk"),
    }
    documents = {
        "index": {
            "manifests": [
                {
                    "digest": "sha256:attestation",
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": "sha256:target",
                    },
                }
            ]
        },
        "attestation": {
            "layers": [
                {
                    "digest": "sha256:statement",
                    "annotations": {"in-toto.io/predicate-type": "https://spdx.dev/Document"},
                }
            ]
        },
        "statement": statement,
    }

    assert extract_buildkit_spdx(documents, image_id="sha256:index") == statement["predicate"]


def test_buildkit_spdx_attestation_rejects_wrong_subject() -> None:
    documents = {
        "index": {
            "manifests": [
                {
                    "digest": "sha256:attestation",
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": "sha256:target",
                    },
                }
            ]
        },
        "attestation": {
            "layers": [
                {
                    "digest": "sha256:statement",
                    "annotations": {"in-toto.io/predicate-type": "https://spdx.dev/Document"},
                }
            ]
        },
        "statement": {
            "predicateType": "https://spdx.dev/Document",
            "subject": [{"digest": {"sha256": "other"}}],
            "predicate": _spdx("vosk"),
        },
    }

    with pytest.raises(ValueError, match="not bound"):
        extract_buildkit_spdx(documents, image_id="sha256:index")
