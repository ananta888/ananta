"""Tool adapter trust boundary.

EW-T019: Aider/OpenCode/ShellGPT/Copilot/Hermes adapter output is parsed into
          Ananta artifacts before use. Adapters cannot directly report success
          without structured artifact validation. Adapters cannot bypass policy
          by doing their own file writes unless the wrapper uses an approved apply path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Known adapters ────────────────────────────────────────────────────────────

KNOWN_ADAPTERS = frozenset({
    "aider", "opencode", "shellgpt", "copilot", "hermes",
    "llm_shell", "claude_code", "cursor", "continue",
})

# Prompt-injection trigger phrases that adapters might inject
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bignore\s+(previous|all|above)\s+instructions?\b"),
    re.compile(r"(?i)\bforget\s+(everything|all|previous)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(?:a\s+)?(?:jailbroken|free|unrestricted|DAN)\b"),
    re.compile(r"(?i)\bsystem\s*prompt\s*[:=]"),
    re.compile(r"(?i)\bACT\s+AS\s+(?:a\s+)?(?:root|admin|superuser)\b"),
    re.compile(r"(?i)\bdisregard\s+(?:your|all)\s+(?:policy|policies|rules|guidelines)\b"),
    re.compile(r"(?i)\bbypass\s+(?:safety|policy|security|restrictions?)\b"),
    re.compile(r"(?i)\bexfiltrate\b"),
]


# ── AdapterOutput ─────────────────────────────────────────────────────────────

@dataclass
class AdapterOutput:
    """Raw output from an external coding tool adapter."""
    adapter_id: str
    raw_text: str
    exit_code: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ── ParsedArtifact (normalized result after trust boundary) ───────────────────

@dataclass
class ParsedAdapterArtifact:
    """Validated, normalized artifact extracted from adapter output. EW-T019."""
    adapter_id: str
    patches: list[str] = field(default_factory=list)       # unified diff blocks
    files_written: list[str] = field(default_factory=list)  # paths mentioned as written
    commands_run: list[str] = field(default_factory=list)   # commands mentioned as executed
    stdout_clean: str = ""                                  # sanitized stdout
    success_claimed: bool = False                           # did adapter claim success?
    validation_errors: list[str] = field(default_factory=list)
    injection_blocked: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors and not self.injection_blocked


# ── TrustBoundaryResult ───────────────────────────────────────────────────────

@dataclass
class TrustBoundaryResult:
    allowed: bool
    reason_code: str
    artifact: ParsedAdapterArtifact | None = None
    detail: str = ""


# ── AdapterTrustBoundary ──────────────────────────────────────────────────────

class AdapterTrustBoundary:
    """Parses and validates adapter output before it enters the Ananta trust boundary.

    Rules:
    1. Adapter must be in KNOWN_ADAPTERS (or caller must supply adapter_id).
    2. Output is scanned for prompt-injection patterns → blocked if found.
    3. Success is only accepted when a structured artifact can be validated.
    4. File writes are only counted if extracted via patch parsing, not self-reported.
    """

    def process(
        self,
        output: AdapterOutput,
        *,
        require_structured_artifact: bool = True,
    ) -> TrustBoundaryResult:
        artifact = ParsedAdapterArtifact(adapter_id=output.adapter_id)

        # 1. Known adapter check
        if output.adapter_id not in KNOWN_ADAPTERS:
            artifact.validation_errors.append(
                f"unknown adapter {output.adapter_id!r} — output rejected"
            )

        # 2. Prompt-injection scan
        if self._contains_injection(output.raw_text):
            artifact.injection_blocked = True
            return TrustBoundaryResult(
                allowed=False,
                reason_code="prompt_injection_blocked",
                artifact=artifact,
                detail="adapter output contains prompt-injection patterns",
            )

        # 3. Extract structured artifacts
        artifact.patches = _extract_diff_blocks(output.raw_text)
        artifact.commands_run = _extract_command_blocks(output.raw_text)
        artifact.stdout_clean = _strip_diff_blocks(output.raw_text)
        artifact.success_claimed = output.exit_code == 0

        # 4. Validate: success requires a structured artifact
        if require_structured_artifact and artifact.success_claimed:
            if not artifact.patches and not artifact.commands_run:
                artifact.validation_errors.append(
                    "adapter claimed success but produced no structured patch or command output"
                )

        if not artifact.is_valid:
            return TrustBoundaryResult(
                allowed=False,
                reason_code="adapter_validation_failed",
                artifact=artifact,
                detail="; ".join(artifact.validation_errors),
            )

        return TrustBoundaryResult(allowed=True, reason_code="adapter_ok", artifact=artifact)

    def _contains_injection(self, text: str) -> bool:
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return True
        return False


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_diff_blocks(text: str) -> list[str]:
    """Extract unified diff blocks from text."""
    blocks = []
    in_block = False
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("--- ") or line.startswith("diff --git"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
            in_block = True
        elif in_block:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _extract_command_blocks(text: str) -> list[str]:
    """Extract ```bash / ```sh code blocks from text."""
    commands = []
    for match in re.finditer(r"```(?:bash|sh)\n(.*?)```", text, re.DOTALL):
        block = match.group(1).strip()
        if block:
            commands.extend(line.strip() for line in block.splitlines() if line.strip())
    return commands


def _strip_diff_blocks(text: str) -> str:
    """Remove diff blocks from text for clean stdout."""
    cleaned = re.sub(r"```(?:diff|patch)\n.*?```", "[patch removed]", text, flags=re.DOTALL)
    return cleaned.strip()
