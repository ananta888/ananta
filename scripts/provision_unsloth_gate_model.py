#!/usr/bin/env python3
"""Provision the pinned local model snapshot used by the Unsloth GPU gate.

The GPU gate (`scripts/run_hub_evidence_unsloth_gpu_gate.py`) admits a local
model directory whose basename is approved by the compatibility matrix
(`tiny-causal-lm`). This script materializes that directory from one pinned,
openly licensed Hugging Face revision and verifies every file against a
pinned SHA-256 before the directory becomes visible.

Chosen snapshot: ``HuggingFaceTB/SmolLM2-135M-Instruct`` (Apache-2.0).

* ``LlamaForCausalLM`` with ``q_proj``/``k_proj``/``v_proj``/``o_proj``, the
  default LoRA target modules of the smoke profile;
* a chat template, so prompt rendering uses the tokenizer path;
* a BPE tokenizer whose pre-tokenizer (``smollm``) the pinned llama.cpp
  converter recognizes, so the GGUF ``q4_k_m`` export is reproducible;
* a real instruction model, so the provider runtime probe receives a
  non-empty completion instead of the noise of a random-weight model.

The resulting directory is runtime data and is never committed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "data/gpu-models/tiny-causal-lm"
APPROVED_BASENAME = "tiny-causal-lm"
_CHUNK_BYTES = 1024 * 1024


class ModelProvisioningError(RuntimeError):
    """Bounded provisioning failure with a stable reason code."""


@dataclass(frozen=True)
class PinnedFile:
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PinnedSnapshot:
    repository: str
    revision: str
    license: str
    files: tuple[PinnedFile, ...]

    def url(self, name: str) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{name}"


SMOLLM2_135M_INSTRUCT = PinnedSnapshot(
    repository="HuggingFaceTB/SmolLM2-135M-Instruct",
    revision="12fd25f77366fa6b3b4b768ec3050bf629380bac",
    license="Apache-2.0",
    files=(
        PinnedFile("config.json", "8eb740e8bbe4cff95ea7b4588d17a2432deb16e8075bc5828ff7ba9be94d982a", 861),
        PinnedFile("generation_config.json", "87b916edaaab66b3899b9d0dd0752727dff6666686da0504d89ae0a6e055a013", 132),
        PinnedFile("merges.txt", "0b54e8aa4e53d5383e2e4bc635a56b43f9647f7b13832d5d9ecd8f82dac4f510", 466391),
        PinnedFile("model.safetensors", "5af571cbf074e6d21a03528d2330792e532ca608f24ac70a143f6b369968ab8c", 269060552),
        PinnedFile("special_tokens_map.json", "2b7379f3ae813529281a5c602bc5a11c1d4e0a99107aaa597fe936c1e813ca52", 655),
        PinnedFile("tokenizer.json", "9ca9acddb6525a194ec8ac7a87f24fbba7232a9a15ffa1af0c1224fcd888e47c", 2104556),
        PinnedFile("tokenizer_config.json", "4ec77d44f62efeb38d7e044a1db318f6a939438425312dfa333b8382dbad98df", 3764),
        PinnedFile("vocab.json", "82b84012e3add4d01d12ba14442026e49b8cbbaead1f79ecf3d919784f82dc79", 800662),
    ),
)

Fetcher = Callable[[str], BinaryIO]


def _https_fetcher(url: str) -> BinaryIO:
    if not url.startswith("https://huggingface.co/"):
        raise ModelProvisioningError("model_provisioning_origin_invalid")
    return urllib.request.urlopen(url, timeout=120)  # noqa: S310 - fixed https origin checked above


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_snapshot(directory: Path, snapshot: PinnedSnapshot = SMOLLM2_135M_INSTRUCT) -> None:
    """Require exactly the pinned regular files with their pinned digests."""
    if directory.is_symlink() or not directory.is_dir():
        raise ModelProvisioningError("model_provisioning_target_invalid")
    expected: Mapping[str, PinnedFile] = {item.name: item for item in snapshot.files}
    present = sorted(candidate.relative_to(directory).as_posix() for candidate in directory.rglob("*"))
    if present != sorted(expected):
        raise ModelProvisioningError("model_provisioning_file_set_mismatch")
    for name, pinned in expected.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ModelProvisioningError("model_provisioning_file_invalid")
        if path.stat().st_size != pinned.size_bytes or _sha256_file(path) != pinned.sha256:
            raise ModelProvisioningError("model_provisioning_digest_mismatch")


def _download(pinned: PinnedFile, url: str, destination: Path, fetcher: Fetcher) -> None:
    digest = hashlib.sha256()
    size = 0
    with fetcher(url) as response, destination.open("wb") as handle:
        for chunk in iter(lambda: response.read(_CHUNK_BYTES), b""):
            size += len(chunk)
            if size > pinned.size_bytes:
                raise ModelProvisioningError("model_provisioning_size_mismatch")
            digest.update(chunk)
            handle.write(chunk)
    if size != pinned.size_bytes or digest.hexdigest() != pinned.sha256:
        raise ModelProvisioningError("model_provisioning_digest_mismatch")


def provision(
    target: Path,
    *,
    snapshot: PinnedSnapshot = SMOLLM2_135M_INSTRUCT,
    fetcher: Fetcher = _https_fetcher,
) -> str:
    """Materialize the pinned snapshot atomically; return ``created`` or ``verified``."""
    if target.name != APPROVED_BASENAME:
        raise ModelProvisioningError("model_provisioning_basename_not_approved")
    if target.exists() or target.is_symlink():
        verify_snapshot(target, snapshot)
        return "verified"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        for pinned in snapshot.files:
            _download(pinned, snapshot.url(pinned.name), staging / pinned.name, fetcher)
            # Unsloth's merged export copies the base shards with shutil.copy2
            # (preserving the mode) into the writable export staging directory
            # and then rewrites them; a read-only snapshot makes that rewrite
            # fail with EACCES. Keep the files owner-writable.
            (staging / pinned.name).chmod(0o644)
        staging.chmod(0o755)
        verify_snapshot(staging, snapshot)
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return "created"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    try:
        outcome = provision(args.target)
    except ModelProvisioningError as exc:
        print(f"model provisioning failed: {exc}", file=sys.stderr)
        return 1
    snapshot = SMOLLM2_135M_INSTRUCT
    print(f"{outcome}: {args.target} <- {snapshot.repository}@{snapshot.revision} ({snapshot.license})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
