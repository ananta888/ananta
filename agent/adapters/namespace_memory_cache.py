"""Bounded namespace/policy-bound file cache. Legacy flat files are never read."""

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Protocol

from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyDecision
from ananta_contracts.memory_access import MemoryOperation, MemoryScope


class CacheMemoryBoundaryPort(Protocol):
    def authorize(self, operation, *, scope) -> MemoryPolicyDecision: ...
    def recheck(self, previous: MemoryPolicyDecision) -> None: ...
    def scoped_id(self, decision: MemoryPolicyDecision, original_id: str) -> str: ...


class NamespaceMemoryCache:
    def __init__(self, root: Path, *, boundary: CacheMemoryBoundaryPort, ttl_seconds=3600, max_entries=256):
        if (type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds)
                or not 1 <= ttl_seconds <= 86400 or type(max_entries) is not int or not 1 <= max_entries <= 4096):
            raise ValueError("memory_cache_configuration_invalid")
        self.root = Path(root)
        self._boundary, self._ttl, self._max = boundary, ttl_seconds, max_entries

    def _directory(self, decision):
        return self.root / self._boundary.scoped_id(decision, "namespace-cache-v2")

    @staticmethod
    def _name(key):
        if not isinstance(key, str) or len(key) > 65536:
            raise ValueError("memory_cache_key_invalid")
        return hashlib.sha256(key.encode()).hexdigest() + ".json"

    def get(self, key, *, manifest_hash=""):
        try:
            decision = self._boundary.authorize(MemoryOperation.read, scope=MemoryScope.run)
            directory = self._directory(decision)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fd = os.open(self._name(key), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as stream:
                    data = stream.read(1048577)
            finally:
                os.close(directory_fd)
            if len(data) > 1048576:
                return None
            entry = json.loads(data)
            if not isinstance(entry, dict):
                return None
            expiry = entry.get("expires_at")
            if (entry.get("schema") != "ananta.namespace-cache.v2"
                    or entry.get("policy_digest") != decision.policy_digest
                    or entry.get("namespace") != list(decision.target.namespace.key(MemoryScope.run))
                    or type(expiry) not in (int, float) or not math.isfinite(expiry)
                    or time.time() >= expiry or expiry > decision.expires_at
                    or manifest_hash and entry.get("manifest_hash") != manifest_hash):
                return None
            payload = entry["payload"]
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if hashlib.sha256(encoded).hexdigest() != entry.get("payload_digest"):
                return None
            self._boundary.recheck(decision)
            return payload
        except (MemoryAccessError, OSError, ValueError, TypeError, KeyError):
            return None

    def _additional(self, operation, previous):
        decision = self._boundary.authorize(operation, scope=MemoryScope.run)
        if (decision.policy_digest != previous.policy_digest
                or decision.target.namespace != previous.target.namespace):
            raise MemoryAccessError("memory_policy_changed")
        self._boundary.recheck(previous)

    def put(self, key, payload, *, manifest_hash=""):
        try:
            decision = self._boundary.authorize(MemoryOperation.append, scope=MemoryScope.run)
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if len(encoded) > 1040000:
                return False
            directory = self._directory(decision)
            if directory.is_symlink():
                return False
            self._boundary.recheck(decision)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            entry = {"schema": "ananta.namespace-cache.v2", "namespace": list(
                decision.target.namespace.key(MemoryScope.run)), "policy_digest": decision.policy_digest,
                "expires_at": min(time.time() + self._ttl, decision.expires_at), "manifest_hash": manifest_hash,
                "payload": payload, "payload_digest": hashlib.sha256(encoded).hexdigest()}
            name = self._name(key)
            updating = (directory / name).exists() or (directory / name).is_symlink()
            if updating:
                self._additional(MemoryOperation.update, decision)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                files = sorted((filename for filename in os.listdir(directory_fd) if filename.endswith(".json")),
                               key=lambda filename: os.stat(filename, dir_fd=directory_fd,
                                                            follow_symlinks=False).st_mtime)
                if not updating and len(files) >= self._max:
                    self._additional(MemoryOperation.delete, decision)
                    while len(files) >= self._max:
                        self._boundary.recheck(decision)
                        os.unlink(files.pop(0), dir_fd=directory_fd)
            finally:
                os.close(directory_fd)
            self._boundary.recheck(decision)
            from agent.adapters.memory_namespace_files import atomic_write_namespace_file

            atomic_write_namespace_file(directory, name, json.dumps(entry, allow_nan=False))
            return True
        except (MemoryAccessError, OSError, ValueError, TypeError):
            return False

    def invalidate(self, key=None):
        try:
            decision = self._boundary.authorize(MemoryOperation.delete, scope=MemoryScope.run)
            directory = self._directory(decision)
            if directory.is_symlink():
                return 0
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                files = [self._name(key)] if key is not None else [
                    name for name in os.listdir(directory_fd) if name.endswith(".json")]
                removed = 0
                for name in files:
                    self._boundary.recheck(decision)
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                        removed += 1
                    except FileNotFoundError:
                        pass
            finally:
                os.close(directory_fd)
            return removed
        except (MemoryAccessError, OSError, ValueError):
            return 0

    def count(self):
        try:
            decision = self._boundary.authorize(MemoryOperation.read, scope=MemoryScope.run)
            directory = self._directory(decision)
            if directory.is_symlink():
                return 0
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                count = sum(name.endswith(".json") for name in os.listdir(directory_fd))
            finally:
                os.close(directory_fd)
            self._boundary.recheck(decision)
            return count
        except (MemoryAccessError, OSError):
            return 0
