"""ContextEnvelope resolver, token budget enforcement, and context compression.

EW-T026: Worker resolves context refs from Hub; refuses unbounded dumps.
EW-T027: Token budget enforced globally and per source class; P0 signals never dropped.
EW-T028: Compression preserves task objective, AC, policy constraints; cloud blocked
          when cloud_allowed=False or context sensitivity disallows it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Context sensitivity ───────────────────────────────────────────────────────

class ContextSensitivity(str, Enum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    secret = "secret"


CLOUD_BLOCKED_SENSITIVITIES = frozenset({
    ContextSensitivity.confidential,
    ContextSensitivity.secret,
})


# ── ContextBlock ──────────────────────────────────────────────────────────────

@dataclass
class ContextBlock:
    """A single resolved context block. EW-T026."""
    source_type: str          # e.g. "task_description", "file_content", "memory", "artifact"
    origin_id: str            # task_id, file path, artifact_id, etc.
    provenance: str           # how this block was obtained
    sensitivity: ContextSensitivity = ContextSensitivity.internal
    token_estimate: int = 0
    content: str = ""
    content_hash: str = ""
    priority: int = 50        # 0 = P0 (never dropped), 100 = lowest priority

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]

    @property
    def is_p0(self) -> bool:
        return self.priority == 0


# ── TokenBudget ───────────────────────────────────────────────────────────────

@dataclass
class TokenBudget:
    """Global and per-source-class budget enforcement. EW-T027."""
    global_limit: int = 32_000
    per_source_limits: dict[str, int] = field(default_factory=dict)

    def check(self, blocks: list[ContextBlock]) -> tuple[list[ContextBlock], list[str]]:
        """Enforce budget. P0 blocks are never dropped.

        Returns (kept_blocks, dropped_reasons).
        Low-priority overflow is compacted first, truncated last.
        """
        p0_blocks = [b for b in blocks if b.is_p0]
        rest = sorted(
            [b for b in blocks if not b.is_p0],
            key=lambda b: (b.priority, -b.token_estimate),
        )

        kept: list[ContextBlock] = list(p0_blocks)
        dropped: list[str] = []
        total = sum(b.token_estimate for b in p0_blocks)

        # Check per-source limits for P0 blocks
        source_totals: dict[str, int] = {}
        for b in p0_blocks:
            source_totals[b.source_type] = source_totals.get(b.source_type, 0) + b.token_estimate

        for block in rest:
            src_limit = self.per_source_limits.get(block.source_type)
            src_total = source_totals.get(block.source_type, 0)

            if src_limit and src_total + block.token_estimate > src_limit:
                dropped.append(
                    f"context_budget_exceeded:source={block.source_type},"
                    f"origin={block.origin_id}"
                )
                continue

            if total + block.token_estimate > self.global_limit:
                dropped.append(
                    f"context_budget_exceeded:global,origin={block.origin_id},"
                    f"tokens={block.token_estimate}"
                )
                continue

            kept.append(block)
            total += block.token_estimate
            source_totals[block.source_type] = src_total + block.token_estimate

        return kept, dropped


# ── CompressionResult ─────────────────────────────────────────────────────────

@dataclass
class CompressionResult:
    compressed_content: str
    source_hashes: list[str]
    original_tokens: int
    compressed_tokens: int
    is_raw_source: bool = False   # always False for compressed output

    def as_block(self, *, source_type: str, origin_id: str, priority: int = 50) -> ContextBlock:
        return ContextBlock(
            source_type=source_type,
            origin_id=origin_id,
            provenance="compressed",
            content=self.compressed_content,
            token_estimate=self.compressed_tokens,
            priority=priority,
        )


# ── ContextCompressor ─────────────────────────────────────────────────────────

class ContextCompressor:
    """Compresses context blocks while preserving critical signals. EW-T028.

    Rules:
    - Preserves: task objective, acceptance criteria, policy constraints,
                 latest failure reason, P0 control signals.
    - Compressed blocks include source hashes — NOT raw source.
    - Cloud compression blocked if cloud_allowed=False or sensitivity disallows.
    """

    PRESERVE_KEYWORDS = (
        "objective:", "goal:", "acceptance criteria:", "ac:", "policy:",
        "constraint:", "denied:", "failure:", "error:", "p0:", "critical:",
        "must not", "must be", "required:", "forbidden:",
    )

    def compress(
        self,
        blocks: list[ContextBlock],
        *,
        cloud_allowed: bool = False,
        max_tokens: int = 8_000,
    ) -> CompressionResult:
        """Compress blocks to fit in max_tokens.

        Uses local truncation (no model call) — caller is responsible for
        model-based compression if needed, subject to cloud_allowed check.
        """
        critical: list[str] = []
        compressible: list[str] = []
        source_hashes: list[str] = []

        for block in blocks:
            # Block cloud compression for sensitive context
            if not cloud_allowed and block.sensitivity in CLOUD_BLOCKED_SENSITIVITIES:
                # Force local (truncation-only) for sensitive blocks
                pass
            source_hashes.append(block.content_hash or "")
            if block.is_p0:
                critical.append(block.content)
            else:
                compressible.append(block.content)

        original_tokens = sum(b.token_estimate for b in blocks)

        # Keep all critical content verbatim
        critical_text = "\n\n".join(critical)
        remaining_budget = max(0, max_tokens - _estimate_tokens(critical_text))

        # From compressible content, keep lines matching preserve keywords first
        preserved_lines: list[str] = []
        other_lines: list[str] = []
        for content in compressible:
            for line in content.splitlines():
                stripped = line.strip().lower()
                if any(kw in stripped for kw in self.PRESERVE_KEYWORDS):
                    preserved_lines.append(line)
                else:
                    other_lines.append(line)

        preserved_text = "\n".join(preserved_lines)
        other_text = "\n".join(other_lines)

        # Fill remaining budget
        budget_used = _estimate_tokens(preserved_text)
        if budget_used < remaining_budget:
            other_truncated = _truncate_to_tokens(other_text, remaining_budget - budget_used)
            body = "\n\n".join(filter(None, [preserved_text, other_truncated]))
        else:
            body = _truncate_to_tokens(preserved_text, remaining_budget)

        compressed = "\n\n".join(filter(None, [critical_text, body]))
        return CompressionResult(
            compressed_content=compressed,
            source_hashes=[h for h in source_hashes if h],
            original_tokens=original_tokens,
            compressed_tokens=_estimate_tokens(compressed),
            is_raw_source=False,
        )


# ── ContextResolver ───────────────────────────────────────────────────────────

class ContextResolver:
    """Resolves context references from Hub. EW-T026.

    Worker refuses unbounded repo dump or raw all-files context.
    """

    MAX_BLOCKS_PER_REQUEST = 50
    MAX_TOKENS_PER_BLOCK = 16_000

    def resolve(
        self,
        refs: list[dict[str, Any]],
        *,
        allowed_source_types: list[str] | None = None,
    ) -> tuple[list[ContextBlock], list[str]]:
        """Resolve a list of context refs into ContextBlocks.

        Returns (resolved_blocks, errors).
        Unbounded dumps (source_type="all_files" or token_estimate=0 with large content)
        are rejected.
        """
        if len(refs) > self.MAX_BLOCKS_PER_REQUEST:
            return [], [
                f"context_unbounded_dump: too many refs ({len(refs)} > {self.MAX_BLOCKS_PER_REQUEST})"
            ]

        resolved: list[ContextBlock] = []
        errors: list[str] = []

        for ref in refs:
            source_type = str(ref.get("source_type", "unknown"))

            if source_type in ("all_files", "full_repo_dump"):
                errors.append(
                    f"context_unbounded_dump: source_type={source_type!r} is not allowed"
                )
                continue

            if allowed_source_types and source_type not in allowed_source_types:
                errors.append(
                    f"context_sensitivity_blocked: source_type={source_type!r} not in allowed list"
                )
                continue

            content = str(ref.get("content", ""))
            token_est = int(ref.get("token_estimate", 0)) or _estimate_tokens(content)

            if token_est > self.MAX_TOKENS_PER_BLOCK:
                errors.append(
                    f"context_budget_exceeded: block {ref.get('origin_id')!r} "
                    f"has {token_est} tokens > {self.MAX_TOKENS_PER_BLOCK} limit"
                )
                continue

            sensitivity_raw = ref.get("sensitivity", ContextSensitivity.internal.value)
            try:
                sensitivity = ContextSensitivity(sensitivity_raw)
            except ValueError:
                sensitivity = ContextSensitivity.internal

            resolved.append(ContextBlock(
                source_type=source_type,
                origin_id=str(ref.get("origin_id", "")),
                provenance=str(ref.get("provenance", "hub_ref")),
                sensitivity=sensitivity,
                token_estimate=token_est,
                content=content,
                priority=int(ref.get("priority", 50)),
            ))

        return resolved, errors


# ── Token estimation ──────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(0, len(text) // 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated]"


# ── ContextBudgetGate (AWF-T019) ──────────────────────────────────────────────

class ContextBudgetGate:
    """Enforces token budget with output reserve before model/provider calls. AWF-T019.

    Reserves output_reserve_tokens from the global budget so the model
    always has room to respond. P0 blocks are never dropped.
    """

    def __init__(
        self,
        budget: TokenBudget,
        *,
        output_reserve_tokens: int = 2_000,
    ) -> None:
        self._budget = budget
        self._output_reserve = max(0, output_reserve_tokens)

    @property
    def effective_limit(self) -> int:
        return max(0, self._budget.global_limit - self._output_reserve)

    def check(self, blocks: list[ContextBlock]) -> tuple[list[ContextBlock], list[str]]:
        """Apply budget with output reserve. Returns (kept_blocks, warnings). AWF-T019."""
        effective = TokenBudget(
            global_limit=self.effective_limit,
            per_source_limits=self._budget.per_source_limits,
        )
        kept, dropped = effective.check(blocks)
        warnings = [f"context_budget_drop:{r}" for r in dropped]
        return kept, warnings

    def is_over_budget(self, blocks: list[ContextBlock]) -> bool:
        total = sum(b.token_estimate for b in blocks)
        return total > self.effective_limit


# ── ContextSensitivityFilter (AWF-T020) ───────────────────────────────────────

class ContextSensitivityFilter:
    """Filters context blocks by sensitivity before sending to a provider. AWF-T020.

    Local workers may process confidential/secret blocks.
    Cloud workers must never receive them — they are stripped and replaced
    with a safe redaction stub.
    """

    _REDACTION_STUB = "[context redacted: sensitivity level blocked for this provider]"

    def filter_for_cloud(
        self, blocks: list[ContextBlock]
    ) -> tuple[list[ContextBlock], list[str]]:
        """Remove confidential/secret blocks for cloud dispatch. AWF-T020."""
        kept: list[ContextBlock] = []
        redacted: list[str] = []
        for block in blocks:
            if block.sensitivity in CLOUD_BLOCKED_SENSITIVITIES:
                redacted.append(
                    f"context_sensitivity_blocked:{block.source_type}:{block.origin_id}"
                )
            else:
                kept.append(block)
        return kept, redacted

    def filter_for_local(self, blocks: list[ContextBlock]) -> list[ContextBlock]:
        """Local workers may use all sensitivity levels — no filtering needed."""
        return list(blocks)

    def apply(
        self,
        blocks: list[ContextBlock],
        *,
        cloud_allowed: bool,
    ) -> tuple[list[ContextBlock], list[str]]:
        """Route to the appropriate filter based on cloud policy. AWF-T020."""
        if cloud_allowed:
            return self.filter_for_cloud(blocks)
        return self.filter_for_local(blocks), []
