"""Restore only the pinned public test model; never mutate a serving volume."""

import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

from tests.meet_ollama_model_spec import MODEL_DIGEST

REGISTRY = "https://registry.ollama.ai/v2/library/qwen2.5"
MANIFEST = "manifests/registry.ollama.ai/library/qwen2.5/1.5b"


def restore_model(directory, *, open_url=urllib.request.urlopen):
    directory = Path(directory)
    if directory.is_symlink() or directory.exists():
        raise ValueError("test_model_destination_not_fresh")
    directory.mkdir(mode=0o755)
    try:
        request = urllib.request.Request(REGISTRY + "/manifests/1.5b", headers={
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        with open_url(request, timeout=20) as response:
            raw = response.read(65_537)
        if len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != MODEL_DIGEST:
            raise ValueError("test_model_manifest_pin_mismatch")
        manifest = json.loads(raw)
        blobs = directory / "blobs"
        blobs.mkdir()
        deadline = time.monotonic() + 600
        for layer in [manifest["config"], *manifest["layers"]]:
            digest, size = layer["digest"], layer["size"]
            if (not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                    or type(size) is not int or not 0 < size <= 1_000_000_000):
                raise ValueError("test_model_blob_invalid")
            target = blobs / digest.replace(":", "-")
            checksum, received = hashlib.sha256(), 0
            with open_url(REGISTRY + "/blobs/" + digest, timeout=20) as response, target.open("xb") as output:
                while chunk := response.read(1_048_576):
                    received += len(chunk)
                    if received > size or time.monotonic() >= deadline:
                        raise ValueError("test_model_download_bound")
                    checksum.update(chunk)
                    output.write(chunk)
            if received != size or checksum.hexdigest() != digest.removeprefix("sha256:"):
                raise ValueError("test_model_blob_pin_mismatch")
        target = directory / MANIFEST
        target.parent.mkdir(parents=True)
        target.write_bytes(raw)
        return {"manifest_digest": MODEL_DIGEST, "blobs": len(manifest["layers"]) + 1}
    except BaseException:
        import shutil

        shutil.rmtree(directory)
        raise


def validate_model(directory):
    directory = Path(directory)
    target = directory / MANIFEST
    if directory.is_symlink() or not directory.is_dir() or target.is_symlink():
        raise ValueError("test_model_directory_invalid")
    raw = target.read_bytes()
    if len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != MODEL_DIGEST:
        raise ValueError("test_model_manifest_pin_mismatch")
    manifest = json.loads(raw)
    for layer in [manifest["config"], *manifest["layers"]]:
        target = directory / "blobs" / layer["digest"].replace(":", "-")
        if target.is_symlink() or not target.is_file() or target.stat().st_size != layer["size"]:
            raise ValueError("test_model_blob_pin_mismatch")
        with target.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != layer["digest"].removeprefix("sha256:"):
            raise ValueError("test_model_blob_pin_mismatch")
    return directory.resolve()
