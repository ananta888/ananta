"""Memoized password key derivation for tests.

werkzeug derives every password hash through ``werkzeug.security._hash_internal(method, salt, password)``,
a pure function; with the default ``scrypt`` parameters one call costs ~80 ms. A test that uses the app
fixture creates the default admin (one derivation) and logs in (one more for the same salt and password,
requested under the canonical method name), which adds up to minutes over the suite. This module wraps that function with a per-process cache: the
results are byte-identical, only repeated derivations of the same (method, salt, password) are skipped.

Every app instance also re-creates the default admin with a fresh random salt, which defeats the cache. The
salt generator werkzeug uses for password hashes (``gen_salt``, used for nothing else here) therefore
returns one fixed random salt per process and length. Trade-off: within a test process equal passwords get
equal hash strings; no test depends on salt randomness (the real generator is werkzeug's, not ours).
``ANANTA_TEST_PASSWORD_HASH_CACHE=0`` switches both off.
"""

from __future__ import annotations

import os
import threading

import werkzeug.security

_original_hash_internal = werkzeug.security._hash_internal
_original_gen_salt = werkzeug.security.gen_salt
_salts: dict[int, str] = {}
_cache: dict[tuple[str, str, str], tuple[str, str]] = {}
_lock = threading.Lock()
_MAX_ENTRIES = 4096


def _cached_hash_internal(method: str, salt: str, password: str) -> tuple[str, str]:
    key = (method, salt, password)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    result = _original_hash_internal(method, salt, password)
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            _cache.clear()
        _cache[key] = result
        # generate_password_hash asks for "scrypt", check_password_hash for the canonical method it stored
        # ("scrypt:32768:8:1"): the same derivation under both names
        _cache[(result[1], salt, password)] = result
    return result


def _process_salt(length: int) -> str:
    with _lock:
        salt = _salts.get(length)
        if salt is None:
            salt = _salts[length] = _original_gen_salt(length)
    return salt


def install() -> None:
    if os.environ.get("ANANTA_TEST_PASSWORD_HASH_CACHE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    werkzeug.security._hash_internal = _cached_hash_internal
    werkzeug.security.gen_salt = _process_salt
