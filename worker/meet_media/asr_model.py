"""Immutable operator-provisioned ASR assets, never a runtime model download."""

import hashlib
from pathlib import Path

REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_ID = "Systran/faster-whisper-small"
FILES = {
    "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
}


def verify_model(directory: Path):
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("meet_asr_model_unavailable")
    if {path.name for path in directory.iterdir()} != set(FILES):
        raise ValueError("meet_asr_model_files_invalid")
    for name, (size, digest) in FILES.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
            raise ValueError("meet_asr_model_file_invalid")
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != digest:
            raise ValueError("meet_asr_model_digest_mismatch")
