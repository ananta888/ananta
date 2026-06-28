"""X86CC-002: Configuration for the x86 CodeCompass extension."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_PROFILES = {
    "x86_64_sysv",
    "x86_64_windows",
    "x86_32_cdecl",
    "x86_32_stdcall",
    "unknown_x86",
}


@dataclass(frozen=True)
class X86Config:
    enabled: bool = False
    raw_assembly_indexing: bool = True
    binary_metadata_indexing: bool = True
    disassembler_export_indexing: bool = True
    cfg_indexing: bool = True
    experimental_adapter: bool = False
    default_profile: str = "x86_64_sysv"
    max_instructions: int = 50_000
    max_functions: int = 5_000
    max_basic_blocks: int = 20_000
    max_strings: int = 10_000
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


def load_x86_config(env: dict[str, str] | None = None) -> X86Config:
    """Load X86Config from environment variables (or supplied dict)."""
    source = env if env is not None else os.environ
    diagnostics: list[str] = []

    enabled = _bool(source.get("ANANTA_CODECOMPASS_X86_ENABLED"), False)
    raw_assembly_indexing = _bool(source.get("ANANTA_CODECOMPASS_X86_RAW_ASSEMBLY"), True)
    binary_metadata_indexing = _bool(source.get("ANANTA_CODECOMPASS_X86_BINARY_METADATA"), True)
    disassembler_export_indexing = _bool(source.get("ANANTA_CODECOMPASS_X86_DISASSEMBLER_EXPORT"), True)
    cfg_indexing = _bool(source.get("ANANTA_CODECOMPASS_X86_CFG"), True)
    experimental_adapter = _bool(source.get("ANANTA_CODECOMPASS_X86_EXPERIMENTAL_ADAPTER"), False)

    raw_profile = str(source.get("ANANTA_CODECOMPASS_X86_DEFAULT_PROFILE") or "x86_64_sysv").strip()
    if raw_profile and raw_profile not in VALID_PROFILES:
        diagnostics.append(f"unsupported_x86_profile:{raw_profile}")
        default_profile = "unknown_x86"
    else:
        default_profile = raw_profile or "x86_64_sysv"

    max_instructions = _int(source.get("ANANTA_CODECOMPASS_X86_MAX_INSTRUCTIONS"), 50_000, diagnostics)
    max_functions = _int(source.get("ANANTA_CODECOMPASS_X86_MAX_FUNCTIONS"), 5_000, diagnostics)
    max_basic_blocks = _int(source.get("ANANTA_CODECOMPASS_X86_MAX_BASIC_BLOCKS"), 20_000, diagnostics)
    max_strings = _int(source.get("ANANTA_CODECOMPASS_X86_MAX_STRINGS"), 10_000, diagnostics)

    return X86Config(
        enabled=enabled,
        raw_assembly_indexing=raw_assembly_indexing,
        binary_metadata_indexing=binary_metadata_indexing,
        disassembler_export_indexing=disassembler_export_indexing,
        cfg_indexing=cfg_indexing,
        experimental_adapter=experimental_adapter,
        default_profile=default_profile,
        max_instructions=max_instructions,
        max_functions=max_functions,
        max_basic_blocks=max_basic_blocks,
        max_strings=max_strings,
        diagnostics=tuple(diagnostics),
    )


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int(raw: str | None, default: int, diagnostics: list[str]) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        diagnostics.append("invalid_integer_config")
        return default
    if value <= 0:
        diagnostics.append("non_positive_integer_config")
        return default
    return min(value, 1_000_000)
