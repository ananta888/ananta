"""Read-only channel catalog. Registration never implies a runtime access grant."""

from __future__ import annotations

import json
from pathlib import Path

from ananta_contracts.memory_access import require_memory_identifier


class MemoryChannelRegistry:
    def __init__(self, identifiers: frozenset[str]):
        if not isinstance(identifiers, frozenset) or not identifiers:
            raise ValueError("memory_channel_registry_invalid")
        for identifier in identifiers:
            require_memory_identifier(identifier, "channel_id")
        self._identifiers = identifiers

    @classmethod
    def from_path(cls, path: Path) -> MemoryChannelRegistry:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("memory_channel_register_too_large")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("memory_channel_register_duplicate_key")
                result[key] = value
            return result

        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if not isinstance(value, dict) or value.get("schema_version") != "ananta.memory-channels.v1":
            raise ValueError("memory_channel_register_schema_invalid")
        channels = value.get("channels")
        if not isinstance(channels, list) or not channels:
            raise ValueError("memory_channel_register_channels_invalid")
        identifiers = []
        for channel in channels:
            if not isinstance(channel, dict):
                raise ValueError("memory_channel_register_entry_invalid")
            identifiers.append(require_memory_identifier(channel.get("id"), "channel_id"))
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("memory_channel_register_duplicate_id")
        return cls(frozenset(identifiers))

    def contains(self, channel_id: str) -> bool:
        return channel_id in self._identifiers
