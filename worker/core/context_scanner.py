"""Prompt injection scanner for context blocks.

EW-T031: Context blocks scanned for injection, exfiltration, hidden unicode,
          steganographic instructions. Blocked blocks replaced by safe stubs.
          Findings appear in TraceBundle and WorkerResult.warnings.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from worker.core.context_resolver import ContextBlock


# ── Injection pattern vocabulary ──────────────────────────────────────────────

@dataclass(frozen=True)
class InjectionPattern:
    name: str
    pattern: re.Pattern[str]
    severity: str   # "high", "medium", "low"


_INJECTION_PATTERNS: list[InjectionPattern] = [
    InjectionPattern("ignore_instructions",
        re.compile(r"(?i)\bignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?|directives?)\b"),
        "high"),
    InjectionPattern("forget_context",
        re.compile(r"(?i)\bforget\s+(everything|all|the\s+above|context|history)\b"),
        "high"),
    InjectionPattern("new_system_prompt",
        re.compile(r"(?i)(system\s*prompt|new\s+instructions?)\s*[:=]"),
        "high"),
    InjectionPattern("act_as_jailbreak",
        re.compile(r"(?i)\byou\s+are\s+(now\s+)?(jailbroken|DAN|free|unrestricted|root|admin)\b"),
        "high"),
    InjectionPattern("privilege_escalation",
        re.compile(r"(?i)\bACT\s+AS\s+(root|admin|superuser|operator)\b"),
        "high"),
    InjectionPattern("policy_bypass",
        re.compile(r"(?i)\b(bypass|disregard|override|ignore)\s+(your\s+)?(safety|policy|policies|rules?|guidelines?|restrictions?)\b"),
        "high"),
    InjectionPattern("exfiltration_instruction",
        re.compile(r"(?i)\b(send|exfiltrate|leak|transmit|upload|POST)\s+(all|any)?\s*(data|content|file|secret|token|key|password)\b"),
        "high"),
    InjectionPattern("hidden_command",
        re.compile(r"(?i)<!--\s*(exec|run|execute|eval)\b"),
        "medium"),
    InjectionPattern("markdown_hidden",
        re.compile(r"\[//\]:\s*#\s*\(.*?(exec|inject|override)"),
        "medium"),
    InjectionPattern("role_injection",
        re.compile(r"(?i)\b(assistant|user|system)\s*:\s*(ignore|forget|bypass|override)\b"),
        "medium"),
    InjectionPattern("latex_injection",
        re.compile(r"\\(?:input|include|write18|immediate)\{"),
        "medium"),
    InjectionPattern("shell_in_context",
        re.compile(r"(?i)\$\(\s*(rm|curl|wget|bash|sh|python|nc|ncat)\b"),
        "medium"),
]

# Hidden/misleading Unicode categories
_SUSPICIOUS_UNICODE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cs"})
_ALLOWED_CONTROL_CHARS = frozenset({"\n", "\r", "\t"})


# ── ScanResult ────────────────────────────────────────────────────────────────

@dataclass
class ScanFinding:
    pattern_name: str
    severity: str
    snippet: str     # up to 80 chars around the match, no full secrets
    position: int


@dataclass
class ContextScanResult:
    block_origin_id: str
    clean: bool
    findings: list[ScanFinding] = field(default_factory=list)
    has_hidden_unicode: bool = False
    safe_stub: ContextBlock | None = None   # replacement when blocked


# ── ContextScanner ────────────────────────────────────────────────────────────

class ContextScanner:
    """Scans context blocks for prompt injection and steganographic instructions.

    Usage:
        scanner = ContextScanner()
        result = scanner.scan(block)
        if not result.clean:
            use result.safe_stub instead of the original block
            add result to trace warnings
    """

    def scan(self, block: ContextBlock) -> ContextScanResult:
        result = ContextScanResult(block_origin_id=block.origin_id, clean=True)

        # 1. Pattern-based injection scan
        for ip in _INJECTION_PATTERNS:
            for match in ip.pattern.finditer(block.content):
                result.clean = False
                start = max(0, match.start() - 20)
                end = min(len(block.content), match.end() + 20)
                snippet = block.content[start:end].replace("\n", " ")[:80]
                result.findings.append(ScanFinding(
                    pattern_name=ip.name,
                    severity=ip.severity,
                    snippet=snippet,
                    position=match.start(),
                ))

        # 2. Hidden/misleading unicode scan
        if self._has_suspicious_unicode(block.content):
            result.clean = False
            result.has_hidden_unicode = True
            result.findings.append(ScanFinding(
                pattern_name="hidden_unicode",
                severity="high",
                snippet="[unicode control/private-use characters detected]",
                position=0,
            ))

        # 3. Build safe stub if not clean
        if not result.clean:
            result.safe_stub = self._make_stub(block, result)

        return result

    def scan_many(
        self,
        blocks: list[ContextBlock],
    ) -> tuple[list[ContextBlock], list[ContextScanResult]]:
        """Scan a list of blocks. Returns (safe_blocks, findings_for_blocked)."""
        safe_blocks: list[ContextBlock] = []
        findings: list[ContextScanResult] = []

        for block in blocks:
            result = self.scan(block)
            if result.clean:
                safe_blocks.append(block)
            else:
                findings.append(result)
                if result.safe_stub:
                    safe_blocks.append(result.safe_stub)

        return safe_blocks, findings

    def to_trace_warnings(self, findings: list[ContextScanResult]) -> list[str]:
        """Format findings for WorkerResult.warnings."""
        warnings = []
        for scan in findings:
            for f in scan.findings:
                warnings.append(
                    f"prompt_injection_blocked:{f.pattern_name}:"
                    f"origin={scan.block_origin_id}:severity={f.severity}"
                )
            if scan.has_hidden_unicode:
                warnings.append(
                    f"hidden_unicode_blocked:origin={scan.block_origin_id}"
                )
        return warnings

    # ── Internals ──────────────────────────────────────────────────────────────

    def _has_suspicious_unicode(self, text: str) -> bool:
        for ch in text:
            if ch in _ALLOWED_CONTROL_CHARS:
                continue
            cat = unicodedata.category(ch)
            if cat in _SUSPICIOUS_UNICODE_CATEGORIES:
                return True
        return False

    def _make_stub(self, block: ContextBlock, result: ContextScanResult) -> ContextBlock:
        from worker.core.context_resolver import ContextSensitivity
        finding_names = ", ".join(f.pattern_name for f in result.findings[:3])
        stub_content = (
            f"[CONTEXT BLOCK BLOCKED: prompt_injection_detected]\n"
            f"origin_id={block.origin_id}\n"
            f"source_type={block.source_type}\n"
            f"findings={finding_names}\n"
            f"provenance={block.provenance}"
        )
        return ContextBlock(
            source_type=block.source_type,
            origin_id=block.origin_id,
            provenance="injection_scanner_stub",
            sensitivity=ContextSensitivity.project_internal,
            token_estimate=10,
            content=stub_content,
            priority=block.priority,
        )
