"""Agent Memory Layers (SIM-014).

Three tiers:
  perception  — raw event impressions for the current tick (cleared each tick)
  short_term  — recent N events (sliding window, included in prompts)
  long_term   — LLM-compressed summary string (updated periodically)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    tick: int
    kind: str       # observation | outcome | social | world
    content: str
    importance: float = 0.5   # 0-1; used when pruning

    def as_dict(self) -> dict[str, Any]:
        return {"tick": self.tick, "kind": self.kind,
                "content": self.content, "importance": self.importance}


class AgentMemory:
    """Per-agent memory manager."""

    def __init__(self, agent_id: str, short_term_capacity: int = 20) -> None:
        self.agent_id = agent_id
        self.short_term_capacity = short_term_capacity
        self._perception: list[MemoryEntry] = []       # current tick
        self._short_term: list[MemoryEntry] = []       # sliding window
        self.long_term_summary: str = ""

    def perceive(self, tick: int, kind: str, content: str, importance: float = 0.5) -> None:
        self._perception.append(MemoryEntry(tick=tick, kind=kind,
                                             content=content, importance=importance))

    def flush_perception(self) -> None:
        """Move perception → short_term at end of tick, prune if needed."""
        self._short_term.extend(self._perception)
        self._perception.clear()
        if len(self._short_term) > self.short_term_capacity:
            # Keep highest-importance entries; never evict current-tick entries
            evictable = sorted(
                self._short_term[:-len(self._perception) or None],
                key=lambda m: m.importance,
            )
            to_drop = len(self._short_term) - self.short_term_capacity
            drop_set = {id(e) for e in evictable[:to_drop]}
            self._short_term = [e for e in self._short_term if id(e) not in drop_set]

    def consolidate(self, summary: str) -> None:
        """Replace long-term summary (called externally by LLM compression step)."""
        self.long_term_summary = summary

    def short_term_for_prompt(self, max_entries: int = 10) -> list[dict[str, Any]]:
        """Return most recent entries for LLM context."""
        return [e.as_dict() for e in self._short_term[-max_entries:]]

    def perception_for_prompt(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self._perception]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "short_term": [e.as_dict() for e in self._short_term],
            "long_term_summary": self.long_term_summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentMemory":
        m = cls(agent_id=d["agent_id"])
        m.long_term_summary = d.get("long_term_summary", "")
        m._short_term = [MemoryEntry(**e) for e in d.get("short_term", [])]
        return m


class MemoryStore:
    """Registry of per-agent memories."""

    def __init__(self, short_term_capacity: int = 20) -> None:
        self._capacity = short_term_capacity
        self._store: dict[str, AgentMemory] = {}

    def get(self, agent_id: str) -> AgentMemory:
        if agent_id not in self._store:
            self._store[agent_id] = AgentMemory(agent_id, self._capacity)
        return self._store[agent_id]

    def flush_all(self) -> None:
        for m in self._store.values():
            m.flush_perception()

    def to_dict(self) -> dict[str, Any]:
        return {aid: m.to_dict() for aid, m in self._store.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryStore":
        store = cls()
        for aid, md in d.items():
            store._store[aid] = AgentMemory.from_dict(md)
        return store
