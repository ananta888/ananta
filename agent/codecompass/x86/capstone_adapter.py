"""X86CC-013: Capstone-based adapter with graceful degradation when capstone unavailable."""
from __future__ import annotations

from typing import Any

from agent.codecompass.x86.adapter import X86DisassemblerAdapter
from agent.codecompass.x86.diagnostics import DISASSEMBLER_UNAVAILABLE, x86_diag_dict
from agent.codecompass.x86.input_taxonomy import X86InputRecord

try:
    import capstone as _capstone  # type: ignore[import]
    _CAPSTONE_AVAILABLE = True
except ImportError:
    _capstone = None  # type: ignore[assignment]
    _CAPSTONE_AVAILABLE = False


def is_available() -> bool:
    """Return True if the capstone library is installed and importable."""
    return _CAPSTONE_AVAILABLE


_CAPSTONE_INPUT_TYPES = frozenset({
    "function_bytes",
    "raw_assembly_text",
    "normalized_assembly",
})

_CAPSTONE_PROFILES = frozenset({
    "x86_64_sysv",
    "x86_64_windows",
    "x86_32_cdecl",
    "x86_32_stdcall",
    "unknown_x86",
})


class CapstoneAdapter(X86DisassemblerAdapter):
    """Adapter using the capstone disassembly library.

    When capstone is not installed, all operations degrade gracefully
    by returning a disassembler_unavailable diagnostic instead of crashing.
    """

    @property
    def name(self) -> str:
        return "capstone"

    @property
    def version(self) -> str:
        if _CAPSTONE_AVAILABLE and _capstone is not None:
            try:
                return f"capstone-{_capstone.__version__}"
            except AttributeError:
                return "capstone-unknown"
        return "capstone-unavailable"

    @property
    def supported_input_types(self) -> frozenset[str]:
        return _CAPSTONE_INPUT_TYPES

    @property
    def supported_profiles(self) -> frozenset[str]:
        return _CAPSTONE_PROFILES

    def capabilities(self) -> dict[str, Any]:
        caps = super().capabilities()
        caps["available"] = _CAPSTONE_AVAILABLE
        return caps

    def disassemble(self, input_record: X86InputRecord) -> dict[str, Any]:
        if not _CAPSTONE_AVAILABLE:
            return {
                "nodes": [],
                "edges": [],
                "diagnostics": [
                    x86_diag_dict(
                        DISASSEMBLER_UNAVAILABLE,
                        "capstone library is not installed; install with: pip install capstone",
                        severity="degraded",
                    )
                ],
                "metadata": {
                    "adapter": self.name,
                    "adapter_version": self.version,
                    "available": False,
                    "instruction_count": 0,
                },
            }

        # When capstone IS available, attempt real disassembly of raw bytes
        if input_record.raw_bytes is None:
            return {
                "nodes": [],
                "edges": [],
                "diagnostics": [x86_diag_dict(DISASSEMBLER_UNAVAILABLE, "raw_bytes required for capstone disassembly", severity="warning")],
                "metadata": {"adapter": self.name, "available": True, "instruction_count": 0},
            }

        try:
            mode = _capstone.CS_MODE_64 if input_record.bitness == 64 else _capstone.CS_MODE_32
            md = _capstone.Cs(_capstone.CS_ARCH_X86, mode)
            md.detail = False
            nodes = []
            base = input_record.base_address or 0
            for instr in md.disasm(input_record.raw_bytes, base):
                nodes.append({
                    "kind": "instruction",
                    "address": instr.address,
                    "mnemonic": instr.mnemonic,
                    "bytes_hex": instr.bytes.hex(" "),
                    "op_str": instr.op_str,
                })
            return {
                "nodes": nodes,
                "edges": [],
                "diagnostics": [],
                "metadata": {
                    "adapter": self.name,
                    "available": True,
                    "instruction_count": len(nodes),
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "nodes": [],
                "edges": [],
                "diagnostics": [x86_diag_dict(DISASSEMBLER_UNAVAILABLE, f"capstone error: {exc}", severity="error")],
                "metadata": {"adapter": self.name, "available": True, "instruction_count": 0},
            }
