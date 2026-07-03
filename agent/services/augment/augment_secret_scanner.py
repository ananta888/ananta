from __future__ import annotations
import re
from dataclasses import dataclass

SECRET_FILE_PATTERNS = [
    ".env", ".env.local", ".env.production", ".pem", ".key", ".p12", ".pfx",
    "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc", "credentials.json",
    "service-account.json", "secrets.yaml", "secrets.json",
]

SECRET_CONTENT_PATTERNS = [
    re.compile(r'(api_?key|apikey|api_?token|access_?token|bearer(?:_token)?)\s*[=:]\s*["\']?([A-Za-z0-9_\-\.]{20,})["\']?', re.IGNORECASE),
    re.compile(r'(password|passwd|secret|credential)\s*[=:]\s*["\']?(\S{8,})["\']?', re.IGNORECASE),
    re.compile(r'(sk-|ghp_|ghs_|github_pat_|xoxb-|xoxa-|xoxp-)([A-Za-z0-9_\-]{20,})'),
    re.compile(r'(AKIA[0-9A-Z]{16})'),  # AWS access key
    re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]


@dataclass
class ScanResult:
    clean: bool
    redacted_text: str
    redaction_count: int
    blocked_files: list[str]
    findings: list[str]


class AugmentSecretScanner:
    """
    AUG-901: Redact secrets from prompts and context before external provider use.
    """

    def scan_and_redact_text(self, text: str) -> ScanResult:
        redacted = text
        count = 0
        findings: list[str] = []
        for pattern in SECRET_CONTENT_PATTERNS:
            def repl(m: re.Match, _pattern: re.Pattern = pattern) -> str:
                nonlocal count
                count += 1
                findings.append(
                    f"found potential secret matching pattern '{_pattern.pattern[:40]}'"
                )
                groups = m.groups()
                if len(groups) >= 2:
                    return f"{groups[0]}=[REDACTED]"
                return "[REDACTED]"
            redacted = pattern.sub(repl, redacted)
        return ScanResult(
            clean=count == 0, redacted_text=redacted,
            redaction_count=count, blocked_files=[], findings=findings,
        )

    def is_secret_file(self, path: str) -> bool:
        path_lower = path.lower()
        for pattern in SECRET_FILE_PATTERNS:
            if path_lower.endswith(pattern) or path_lower.split("/")[-1] == pattern:
                return True
        return False

    def filter_paths(self, paths: list[str]) -> tuple[list[str], list[str]]:
        """Returns (allowed, blocked)."""
        allowed = [p for p in paths if not self.is_secret_file(p)]
        blocked = [p for p in paths if self.is_secret_file(p)]
        return allowed, blocked

    def prepare_prompt_for_external(
        self, prompt: str, *, context_snippets: list[str]
    ) -> tuple[str, list[str], int]:
        """Returns (clean_prompt, clean_snippets, total_redactions)."""
        total = 0
        prompt_result = self.scan_and_redact_text(prompt)
        total += prompt_result.redaction_count
        clean_snippets = []
        for snippet in context_snippets:
            r = self.scan_and_redact_text(snippet)
            total += r.redaction_count
            clean_snippets.append(r.redacted_text)
        return prompt_result.redacted_text, clean_snippets, total
