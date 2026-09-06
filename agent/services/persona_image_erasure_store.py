"""Descriptor-confined removal of exactly one immutable v1 persona PNG."""

import hashlib
import os
import re
import stat
from pathlib import Path


class PersonaImageErasureStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        if not self.base_dir.is_absolute():
            raise ValueError("persona_erasure_store_absolute_path_required")

    def erase(self, reference, expected_size, *, checkpoint):
        if (
            reference.kind != "image"
            or reference.revision != 1
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", reference.artifact_id)
            or not re.fullmatch(r"[a-f0-9]{64}", reference.sha256)
            or type(expected_size) is not int
            or not 0 < expected_size <= 5 * 1024 * 1024
        ):
            raise ValueError("persona_erasure_reference_invalid")
        checkpoint()
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        base = directory = descriptor = None
        filename = "v0001__image.png"
        try:
            base = os.open(self.base_dir, directory_flags)
            try:
                directory = os.open(reference.artifact_id, directory_flags, dir_fd=base)
            except FileNotFoundError:
                checkpoint()
                return
            try:
                descriptor = os.open(filename, file_flags, dir_fd=directory)
            except FileNotFoundError:
                checkpoint()
                return
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_size != expected_size or observed.st_nlink != 1:
                raise ValueError("persona_erasure_file_changed")
            digest = hashlib.sha256()
            read = 0
            while read <= expected_size:
                content = os.read(descriptor, min(65536, expected_size + 1 - read))
                if not content:
                    break
                read += len(content)
                digest.update(content)
            if read != expected_size or digest.hexdigest() != reference.sha256:
                raise ValueError("persona_erasure_file_changed")
            checkpoint()
            current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_nlink) != (
                observed.st_dev,
                observed.st_ino,
                observed.st_size,
                observed.st_mtime_ns,
                1,
            ):
                raise ValueError("persona_erasure_file_changed")
            os.unlink(filename, dir_fd=directory)
            os.fsync(directory)
        except OSError:
            raise ValueError("persona_erasure_storage_unavailable") from None
        finally:
            for handle in (descriptor, directory, base):
                if handle is not None:
                    os.close(handle)
