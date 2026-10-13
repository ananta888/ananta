"""Atomic file port; callers own namespace authorization and configured roots."""


def atomic_write_namespace_file(directory, name, content):
    import os
    import uuid

    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("memory_file_name_invalid")
    # Pin the parent directory and avoid following attacker-created file symlinks.
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".export-" + uuid.uuid4().hex
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
