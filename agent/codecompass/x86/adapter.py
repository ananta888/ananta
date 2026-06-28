"""X86CC-011: Abstract base class for x86 disassembler adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent.codecompass.x86.input_taxonomy import X86InputRecord


class X86DisassemblerAdapter(ABC):
    """Abstract contract for x86 disassembler adapters.

    Contract:
    - No binary execution — static analysis only
    - Must be fixture-capable (can operate on JSON fixtures)
    - disassemble() returns dict with nodes, edges, diagnostics
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter name identifier."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Adapter version string."""
        ...

    @property
    @abstractmethod
    def supported_input_types(self) -> frozenset[str]:
        """Set of supported input kind strings."""
        ...

    @property
    @abstractmethod
    def supported_profiles(self) -> frozenset[str]:
        """Set of supported architecture profiles."""
        ...

    def capabilities(self) -> dict[str, Any]:
        """Return adapter capabilities metadata."""
        return {
            "name": self.name,
            "version": self.version,
            "supported_input_types": sorted(self.supported_input_types),
            "supported_profiles": sorted(self.supported_profiles),
            "execution_blocked": True,
        }

    @abstractmethod
    def disassemble(self, input_record: X86InputRecord) -> dict[str, Any]:
        """Disassemble the given input; return nodes, edges, diagnostics.

        Returns dict with keys:
          - nodes: list of node dicts
          - edges: list of edge dicts
          - diagnostics: list of diagnostic dicts
          - metadata: dict with profile, adapter, instruction_count, etc.
        """
        ...


class DummyAdapter(X86DisassemblerAdapter):
    """Concrete adapter that returns empty but valid records. Used for testing."""

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def version(self) -> str:
        return "0.0.1"

    @property
    def supported_input_types(self) -> frozenset[str]:
        from agent.codecompass.x86.input_taxonomy import ALL_INPUT_KINDS
        return frozenset(ALL_INPUT_KINDS)

    @property
    def supported_profiles(self) -> frozenset[str]:
        from agent.codecompass.x86.config import VALID_PROFILES
        return frozenset(VALID_PROFILES)

    def disassemble(self, input_record: X86InputRecord) -> dict[str, Any]:
        return {
            "nodes": [],
            "edges": [],
            "diagnostics": [],
            "metadata": {
                "profile": input_record.abi,
                "adapter": self.name,
                "adapter_version": self.version,
                "instruction_count": 0,
                "function_count": 0,
                "basic_block_count": 0,
                "source_path": input_record.source_path,
            },
        }
