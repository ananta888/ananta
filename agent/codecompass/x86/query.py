"""X86CC-019: Query model and engine for x86 CodeCompass extension."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.codecompass.x86.graph_extensions import X86GraphIndex

VALID_QUERY_KINDS = {
    "address",
    "symbol",
    "function",
    "mnemonic",
    "import",
    "string",
    "section",
    "basic_block",
    "diagnostics",
}


@dataclass
class X86Query:
    kind: str  # address/symbol/function/mnemonic/import/string/section/basic_block/diagnostics
    value: str = ""
    profile: str = ""
    file: str = ""
    section: str = ""
    address_range: tuple[int, int] | None = None
    node_kind: str = ""
    limit: int = 50


@dataclass
class X86QueryResult:
    query: X86Query
    node_ids: list[str] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None


def parse_address(s: str) -> int | None:
    """Parse a hex (0x...) or decimal address string; return int or None on failure."""
    s = str(s or "").strip()
    if not s:
        return None
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s, 10)
    except ValueError:
        return None


class X86QueryEngine:
    """Executes X86Query against an X86GraphIndex."""

    def __init__(self, nodes_by_id: dict[str, dict[str, Any]] | None = None) -> None:
        self._nodes_by_id: dict[str, dict[str, Any]] = nodes_by_id or {}

    def execute(self, query: X86Query, graph_index: X86GraphIndex) -> X86QueryResult:
        """Execute the query and return an X86QueryResult."""
        if query.kind not in VALID_QUERY_KINDS:
            return X86QueryResult(
                query=query,
                status="error",
                error=f"invalid_query_kind:{query.kind}",
                warnings=[f"valid_kinds:{','.join(sorted(VALID_QUERY_KINDS))}"],
            )

        node_ids: list[str] = []
        warnings: list[str] = []

        if query.kind == "address":
            addr = parse_address(query.value)
            if addr is None:
                return X86QueryResult(
                    query=query,
                    status="error",
                    error="invalid_address_query",
                    warnings=[f"could not parse address: {query.value!r}"],
                )
            addr_hex = hex(addr)
            node_ids = list(graph_index.by_address.get(addr_hex, []))
            if not node_ids:
                warnings.append("address_unresolved")

        elif query.kind == "symbol":
            node_ids = list(graph_index.by_symbol.get(query.value, []))
        elif query.kind == "function":
            node_ids = list(graph_index.by_function.get(query.value, []))
            if not node_ids:
                # try as address
                addr = parse_address(query.value)
                if addr is not None:
                    node_ids = list(graph_index.by_function.get(hex(addr), []))
        elif query.kind == "mnemonic":
            node_ids = list(graph_index.by_mnemonic.get(query.value.lower(), []))
            if not node_ids:
                # case-insensitive search
                lower_val = query.value.lower()
                for k, v in graph_index.by_mnemonic.items():
                    if k.lower() == lower_val:
                        node_ids.extend(v)
        elif query.kind == "import":
            node_ids = list(graph_index.by_import.get(query.value, []))
        elif query.kind == "string":
            # substring match
            for k, v in graph_index.by_string.items():
                if query.value in k:
                    node_ids.extend(v)
        elif query.kind == "section":
            node_ids = list(graph_index.by_section.get(query.value, []))
        elif query.kind == "basic_block":
            addr = parse_address(query.value)
            if addr is not None:
                node_ids = list(graph_index.by_basic_block.get(hex(addr), []))
            else:
                node_ids = list(graph_index.by_basic_block.get(query.value, []))
        elif query.kind == "diagnostics":
            # Return all diagnostic nodes
            node_ids = [
                nid for nid, n in self._nodes_by_id.items()
                if n.get("kind") == "x86_diagnostic"
            ]

        # Deduplicate, sort, bound
        seen: set[str] = set()
        unique_ids: list[str] = []
        for nid in node_ids:
            if nid not in seen:
                seen.add(nid)
                unique_ids.append(nid)
        unique_ids = sorted(unique_ids)

        limit = max(1, min(query.limit, 500))
        if len(unique_ids) > limit:
            warnings.append(f"results_truncated:{len(unique_ids)}->{limit}")
            unique_ids = unique_ids[:limit]

        nodes = [self._nodes_by_id[nid] for nid in unique_ids if nid in self._nodes_by_id]

        return X86QueryResult(
            query=query,
            node_ids=unique_ids,
            nodes=nodes,
            warnings=warnings,
            status="ok",
        )
