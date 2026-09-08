"""Build-only exact Python inventory check; no task, model or Hub dependency."""

import os
import re
import stat
import sys
from importlib.metadata import distributions

MAX_LOCK_BYTES = 16 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
REQUIRED = frozenset({"pip", "piper-tts", "playwright", "onnxruntime", "onnxruntime-gpu"})


def _entry(name, version):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("meet_runtime_lock_invalid")
    if not isinstance(version, str) or len(version) > 40 or not VERSION.fullmatch(version):
        raise ValueError("meet_runtime_lock_invalid")
    return re.sub(r"[-_.]+", "-", name).lower(), version


def parse_lock(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_LOCK_BYTES:
        raise ValueError("meet_runtime_lock_invalid")
    result = {}
    for line in raw.decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("==")
        if len(fields) != 2:
            raise ValueError("meet_runtime_lock_invalid")
        name, version = _entry(*fields)
        if name in result or len(result) >= 256:
            raise ValueError("meet_runtime_lock_invalid")
        result[name] = version
    if not REQUIRED <= result.keys():
        raise ValueError("meet_runtime_lock_invalid")
    return result


def installed_inventory(packages):
    result = {}
    for package in packages:
        name, version = _entry(package.metadata.get("Name"), package.version)
        if name in result or len(result) >= 256:
            raise ValueError("meet_runtime_inventory_invalid")
        result[name] = version
    return result


def runtime_matches(expected, installed):
    # This is the only intentional dependency substitution. CPU ORT must be
    # absent, not accepted as an alternative provider or a fallback runtime.
    return installed == {name: version for name, version in expected.items() if name != "onnxruntime"}


def read_lock(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_LOCK_BYTES:
            raise ValueError("meet_runtime_lock_invalid")
        raw = os.read(descriptor, MAX_LOCK_BYTES + 1)
        if len(raw) != metadata.st_size:
            raise ValueError("meet_runtime_lock_invalid")
        return parse_lock(raw)
    finally:
        os.close(descriptor)


def main(args=None):
    args = sys.argv[1:] if args is None else args
    try:
        if len(args) != 1:
            raise ValueError("meet_runtime_lock_invalid")
        valid = runtime_matches(read_lock(args[0]), installed_inventory(distributions()))
    except (OSError, ValueError, TypeError, AttributeError):
        valid = False
    print("meet-runtime-lock-ok" if valid else "meet-runtime-lock-failed")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
