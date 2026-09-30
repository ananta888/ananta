"""Bounded LRU cache for decoded CodeCompass domain supplement records."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from threading import RLock

from agent.services.codecompass_domain_supplement_models import (
    CachedDomainSupplementRecords,
    CodeCompassDomainSupplementBinding,
)

_CachedDomain = CachedDomainSupplementRecords


class CodeCompassDomainSupplementDomainCache:
    """Keep recently decoded domains within an entry and byte budget."""

    def __init__(
        self,
        *,
        maximum_cached_domains: int,
        maximum_cached_bytes: int,
    ) -> None:
        self._maximum_cached_domains = int(maximum_cached_domains)
        self._maximum_cached_bytes = int(maximum_cached_bytes)
        self._cached_bytes = 0
        self._domain_cache: OrderedDict[
            tuple[str, str, str],
            _CachedDomain,
        ] = OrderedDict()
        self._cache_lock = RLock()

    def get(
        self,
        *,
        binding: CodeCompassDomainSupplementBinding,
        domain_key: str,
    ) -> _CachedDomain | None:
        key = (
            binding.artifact_sha256,
            binding.logical_content_hash,
            domain_key,
        )
        with self._cache_lock:
            cached = self._domain_cache.pop(key, None)
            if cached is not None:
                self._domain_cache[key] = cached
            return cached

    def put(
        self,
        *,
        binding: CodeCompassDomainSupplementBinding,
        domain_key: str,
        records: tuple[
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
        ],
        raw_size: int,
    ) -> None:
        if raw_size > self._maximum_cached_bytes:
            return
        key = (
            binding.artifact_sha256,
            binding.logical_content_hash,
            domain_key,
        )
        with self._cache_lock:
            replaced = self._domain_cache.pop(key, None)
            if replaced is not None:
                self._cached_bytes -= replaced.raw_size
            cached = _CachedDomain(records=records, raw_size=raw_size)
            self._domain_cache[key] = cached
            self._cached_bytes += raw_size
            while (
                len(self._domain_cache) > self._maximum_cached_domains
                or self._cached_bytes > self._maximum_cached_bytes
            ):
                _evicted_key, evicted = self._domain_cache.popitem(last=False)
                self._cached_bytes -= evicted.raw_size
