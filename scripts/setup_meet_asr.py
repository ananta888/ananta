#!/usr/bin/env python3
"""Explicitly provision the pinned local Whisper-small model; no Hub activation."""

import argparse
import hashlib
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker.meet_media.asr_model import FILES, MODEL_ID, REVISION, verify_model


def provision(directory: Path):
    if (
        not directory.is_absolute()
        or not directory.is_dir()
        or directory.is_symlink()
        or directory.stat().st_mode & 0o077
    ):
        raise ValueError("meet_asr_runtime_directory_must_be_private")
    models = directory / "models"
    if models.is_symlink():
        raise ValueError("meet_asr_models_symlink_denied")
    models.mkdir(exist_ok=True)
    target = models / "faster-whisper-small"
    if target.exists():
        verify_model(target)
        print("Pinned local ASR model already verified.")
        return
    with tempfile.TemporaryDirectory(prefix=".asr-download-", dir=models) as temporary:
        staged = Path(temporary) / "model"
        staged.mkdir()
        for name, (size, expected) in FILES.items():
            start, received = time.monotonic(), 0
            digest = hashlib.sha256()
            url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response, (staged / name).open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > size or time.monotonic() - start > 600:
                        raise ValueError("meet_asr_download_budget_exceeded")
                    digest.update(chunk)
                    output.write(chunk)
            if received != size or digest.hexdigest() != expected:
                raise ValueError("meet_asr_download_integrity_failed")
        verify_model(staged)
        # Claim the destination atomically; hard links never replace existing
        # files. A concurrent verifier fails closed until publication finishes.
        target.mkdir(mode=0o700)
        for name in FILES:
            os.link(staged / name, target / name)
    print("Pinned Whisper-small model ready. No receive or provider policy activated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    provision(parser.parse_args().directory)
