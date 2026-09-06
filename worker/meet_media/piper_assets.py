"""Read bounded pinned voice bytes before handing any input to a model parser."""

import hashlib
import os
import stat
from pathlib import Path

from ananta_contracts.meet_speech import MODEL_NAME, speech_profile, validate_speech_profile


def read_pinned_file(path, *, sha256, maximum):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum or before.st_nlink != 1:
            raise ValueError("meet_piper_asset_invalid")
        chunks, size, digest = [], 0, hashlib.sha256()
        while size <= before.st_size:
            part = os.read(descriptor, min(1_048_576, before.st_size + 1 - size))
            if not part:
                break
            chunks.append(part)
            size += len(part)
            digest.update(part)
        after = os.fstat(descriptor)
        if (
            size != before.st_size
            or digest.hexdigest() != sha256
            or (before.st_mtime_ns, before.st_ctime_ns, before.st_nlink, before.st_size)
            != (after.st_mtime_ns, after.st_ctime_ns, after.st_nlink, after.st_size)
        ):
            raise ValueError("meet_piper_asset_changed")
        return b"".join(chunks)
    except OSError:
        raise ValueError("meet_piper_asset_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_pinned_assets(profile=None):
    profile = validate_speech_profile(profile if profile is not None else speech_profile())
    path = Path(os.environ.get("MEET_PIPER_MODEL", "/models/" + MODEL_NAME))
    if not path.is_absolute():
        raise ValueError("meet_piper_model_path_invalid")
    # Only verified byte snapshots, never these mutable pathnames, are passed
    # to Piper/ONNX. A replacement after this read cannot swap the loaded model.
    return (
        read_pinned_file(path, sha256=profile["model_sha256"], maximum=70_000_000),
        read_pinned_file(str(path) + ".json", sha256=profile["config_sha256"], maximum=65_536),
    )
