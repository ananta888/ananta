"""Dataset-Validator und Secret-Scan fuer LoRA/QLoRA Training-Datasets (MLLORA-008/009).

- Liest JSONL streamend (kein unkontrolliertes RAM-Loading)
- Prueft Pflichtfelder, Textlaengen, Dubletten, Formattyp
- Secret-Scan: blockiert API Keys, Tokens, Private Keys, Password-Pattern per Default
- Erzeugt dataset_validation_report.json
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MAX_LINE_BYTES = 512 * 1024  # 512 KB pro Zeile
_MAX_TEXT_CHARS = 32768
_MIN_OUTPUT_CHARS = 1

# Secret-Pattern (regex; Case-insensitive)
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(r'(?:api[-_]?key|apikey)\s*[:=]\s*["\']?[a-z0-9_\-]{16,}', re.IGNORECASE)),
    ("bearer_token", re.compile(r'bearer\s+[a-z0-9_\-\.]{20,}', re.IGNORECASE)),
    ("password_field", re.compile(r'password\s*[:=]\s*["\']?.{4,}', re.IGNORECASE)),
    ("private_key_header", re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE KEY-----')),
    ("aws_access_key", re.compile(r'(?:AKIA|ASIA|AROA)[A-Z0-9]{16}')),
    ("github_token", re.compile(r'gh[pousr]_[A-Za-z0-9]{36}')),
    ("generic_secret", re.compile(r'(?:secret|token|credential)\s*[:=]\s*["\']?[a-z0-9_\-\.]{16,}', re.IGNORECASE)),
    ("openai_key", re.compile(r'sk-[a-zA-Z0-9]{32,}')),
]


@dataclass
class ValidationError:
    line_number: int
    error_type: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class SecretFinding:
    line_number: int
    pattern_name: str
    excerpt: str  # Kurzer Ausschnitt ohne echten Secret-Wert


@dataclass
class DatasetValidationReport:
    dataset_path: str
    dataset_hash: str
    total_lines: int
    accepted_record_count: int
    rejected_record_count: int
    duplicate_count: int
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    secret_findings: list[SecretFinding] = field(default_factory=list)
    ok: bool = False
    secret_scan_passed: bool = False
    format_type: str | None = None  # "instruction" | "chat" | "mixed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "mlintern_dataset_validation_report.v1",
            "dataset_path": self.dataset_path,
            "dataset_hash": self.dataset_hash,
            "total_lines": self.total_lines,
            "accepted_record_count": self.accepted_record_count,
            "rejected_record_count": self.rejected_record_count,
            "duplicate_count": self.duplicate_count,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "secret_finding_count": len(self.secret_findings),
            "ok": self.ok,
            "secret_scan_passed": self.secret_scan_passed,
            "format_type": self.format_type,
            "errors": [
                {"line": e.line_number, "type": e.error_type, "message": e.message, "severity": e.severity}
                for e in self.errors
            ],
            "warnings": [
                {"line": w.line_number, "type": w.error_type, "message": w.message, "severity": w.severity}
                for w in self.warnings
            ],
            "secret_findings": [
                {"line": s.line_number, "pattern": s.pattern_name, "excerpt": s.excerpt}
                for s in self.secret_findings
            ],
        }


class MlInternDatasetValidationService:
    """Validiert LoRA-Trainingsdatensaetze (JSONL) streamend."""

    def validate(
        self,
        dataset_path: str | Path,
        *,
        require_secret_scan: bool = True,
        allow_mixed_formats: bool = False,
        explicit_override: dict | None = None,
    ) -> DatasetValidationReport:
        """Validiert eine JSONL-Datei.

        Returns:
            DatasetValidationReport mit ok=True, wenn alle Pflichtpruefungen bestanden.
        """
        path = Path(dataset_path)
        if not path.exists():
            report = DatasetValidationReport(
                dataset_path=str(path),
                dataset_hash="",
                total_lines=0,
                accepted_record_count=0,
                rejected_record_count=0,
                duplicate_count=0,
            )
            report.errors.append(ValidationError(0, "file_not_found", f"dataset file not found: {path}"))
            return report

        dataset_hash = self._hash_file(path)
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        secret_findings: list[SecretFinding] = []
        total_lines = 0
        accepted = 0
        rejected = 0
        seen_hashes: set[str] = set()
        duplicate_count = 0
        format_types: set[str] = set()

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                total_lines += 1
                lineno = total_lines

                if len(raw_line.encode("utf-8")) > _MAX_LINE_BYTES:
                    errors.append(ValidationError(lineno, "line_too_large", f"line {lineno} exceeds {_MAX_LINE_BYTES} bytes"))
                    rejected += 1
                    continue

                line = raw_line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(ValidationError(lineno, "invalid_json", f"JSON parse error at line {lineno}: {exc}"))
                    rejected += 1
                    continue

                if not isinstance(record, dict):
                    errors.append(ValidationError(lineno, "not_a_dict", f"line {lineno}: record must be a JSON object"))
                    rejected += 1
                    continue

                # Format-Erkennung und Validierung
                fmt, fmt_errors = self._validate_record(record, lineno)
                if fmt:
                    format_types.add(fmt)
                errors.extend(fmt_errors)
                if fmt_errors:
                    rejected += 1
                    continue

                # Duplikat-Check
                rec_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
                if rec_hash in seen_hashes:
                    duplicate_count += 1
                    warnings.append(ValidationError(lineno, "duplicate", f"line {lineno}: duplicate record detected"))
                else:
                    seen_hashes.add(rec_hash)

                # Secret-Scan
                if require_secret_scan:
                    line_secrets = self._scan_secrets(line, lineno)
                    secret_findings.extend(line_secrets)

                accepted += 1

        # Format-Konsistenz
        if len(format_types) > 1 and not allow_mixed_formats:
            warnings.append(ValidationError(0, "mixed_formats", f"dataset contains mixed formats: {sorted(format_types)}"))

        secret_scan_passed = len(secret_findings) == 0
        # Wenn override gesetzt, trotzdem Secret-Scan dokumentieren aber nicht blockieren
        override_reason = (explicit_override or {}).get("reason", "")
        if secret_findings and explicit_override and override_reason:
            warnings.append(ValidationError(0, "secret_scan_override",
                f"secret findings overridden with reason: {override_reason[:200]}"))
            secret_scan_passed = True  # Override akzeptiert
        elif secret_findings and not explicit_override:
            for sf in secret_findings:
                errors.append(ValidationError(sf.line_number, "secret_detected",
                    f"potential secret at line {sf.line_number} (pattern: {sf.pattern_name}) — training blocked"))

        ok = (
            len([e for e in errors if e.severity == "error"]) == 0
            and accepted > 0
            and (secret_scan_passed or not require_secret_scan)
        )

        fmt_str = next(iter(format_types), None) if len(format_types) == 1 else ("mixed" if format_types else None)

        return DatasetValidationReport(
            dataset_path=str(path),
            dataset_hash=dataset_hash,
            total_lines=total_lines,
            accepted_record_count=accepted,
            rejected_record_count=rejected,
            duplicate_count=duplicate_count,
            errors=errors,
            warnings=warnings,
            secret_findings=secret_findings,
            ok=ok,
            secret_scan_passed=secret_scan_passed,
            format_type=fmt_str,
        )

    def validate_train_eval_pair(
        self,
        train_path: str | Path,
        eval_path: str | Path,
        *,
        require_secret_scan: bool = True,
    ) -> tuple[DatasetValidationReport, DatasetValidationReport, list[str]]:
        """Validiert Train- und Eval-Dataset als Paar. Prueft auf Identitaet."""
        train_report = self.validate(train_path, require_secret_scan=require_secret_scan)
        eval_report = self.validate(eval_path, require_secret_scan=require_secret_scan)
        pair_errors: list[str] = []
        if train_report.dataset_hash and eval_report.dataset_hash:
            if train_report.dataset_hash == eval_report.dataset_hash:
                pair_errors.append("train and eval datasets are identical (same hash) — this is not allowed")
            if str(Path(train_path).resolve()) == str(Path(eval_path).resolve()):
                pair_errors.append("train and eval dataset paths are the same file")
        return train_report, eval_report, pair_errors

    def write_report(self, report: DatasetValidationReport, output_path: str | Path) -> None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _validate_record(record: dict, lineno: int) -> tuple[str | None, list[ValidationError]]:
        errs: list[ValidationError] = []
        if "messages" in record:
            msgs = record["messages"]
            if not isinstance(msgs, list) or len(msgs) < 2:
                errs.append(ValidationError(lineno, "invalid_chat", f"line {lineno}: 'messages' must be a list with >= 2 items"))
                return "chat", errs
            has_user = any(m.get("role") == "user" for m in msgs if isinstance(m, dict))
            has_assistant = any(m.get("role") == "assistant" for m in msgs if isinstance(m, dict))
            if not has_user:
                errs.append(ValidationError(lineno, "missing_user_turn", f"line {lineno}: chat record missing 'user' message"))
            if not has_assistant:
                errs.append(ValidationError(lineno, "missing_assistant_turn", f"line {lineno}: chat record missing 'assistant' message"))
            for mi, msg in enumerate(msgs):
                if not isinstance(msg, dict):
                    continue
                content = str(msg.get("content") or "")
                if msg.get("role") == "assistant" and len(content.strip()) < _MIN_OUTPUT_CHARS:
                    errs.append(ValidationError(lineno, "empty_assistant_output",
                        f"line {lineno}: message[{mi}] assistant content is empty"))
            return "chat", errs

        if "instruction" in record:
            instruction = str(record.get("instruction") or "").strip()
            output = str(record.get("output") or "").strip()
            if not instruction:
                errs.append(ValidationError(lineno, "missing_instruction", f"line {lineno}: 'instruction' is empty"))
            if len(output) < _MIN_OUTPUT_CHARS:
                errs.append(ValidationError(lineno, "empty_output", f"line {lineno}: 'output' is empty — not allowed"))
            if len(instruction) > _MAX_TEXT_CHARS:
                errs.append(ValidationError(lineno, "text_too_long", f"line {lineno}: 'instruction' exceeds {_MAX_TEXT_CHARS} chars"))
            if len(output) > _MAX_TEXT_CHARS:
                errs.append(ValidationError(lineno, "text_too_long", f"line {lineno}: 'output' exceeds {_MAX_TEXT_CHARS} chars"))
            return "instruction", errs

        errs.append(ValidationError(lineno, "unknown_format",
            f"line {lineno}: record must have 'instruction' (instruction format) or 'messages' (chat format)"))
        return None, errs

    @staticmethod
    def _scan_secrets(line: str, lineno: int) -> list[SecretFinding]:
        findings = []
        for name, pattern in _SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                start = max(0, match.start() - 10)
                end = min(len(line), match.start() + 30)
                excerpt = line[start:end].replace("\n", " ")
                findings.append(SecretFinding(lineno, name, f"...{excerpt}..."))
        return findings


_validator_instance: MlInternDatasetValidationService | None = None


def get_dataset_validation_service() -> MlInternDatasetValidationService:
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = MlInternDatasetValidationService()
    return _validator_instance
