"""HDE-010/HDE-011: ToolProposal artifact (``tool_proposal.v1``).

LLMs and users may *propose* tools — never register or activate them
(HDE-DD-003). A proposal is a validated, digest-bound JSON artifact that
must pass schema validation, test validation and the approval lifecycle
before the promotion service may activate it
(``agent/services/custom_tool_promotion_service.py``).

The schema deliberately forbids free shell strings: execution is either
a ``command_template`` token list with typed argument placeholders or a
``script_body_ref`` inside the approved script store. Static tokens may
not contain shell metacharacters; the final rendered command still
passes the ShellCommandAnalyzer at execution time (HDE-016).

Contract: ``docs/contracts/tool-proposal-artifact.md``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

TOOL_PROPOSAL_SCHEMA = "tool_proposal.v1"

STATUS_PENDING = "pending"
STATUS_VALIDATION_FAILED = "validation_failed"
STATUS_VALIDATED = "validated"
STATUS_APPROVAL_REQUIRED = "approval_required"
STATUS_APPROVED = "approved"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_REJECTED = "rejected"

KNOWN_STATUSES = {
    STATUS_PENDING,
    STATUS_VALIDATION_FAILED,
    STATUS_VALIDATED,
    STATUS_APPROVAL_REQUIRED,
    STATUS_APPROVED,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_REJECTED,
}

_NAME_RE = re.compile(r"^(custom|project)\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_SHELL_METACHARS = (";", "|", "&", "`", "$(", "${", "<", ">", "\n", "\x00")
_VALID_RISK_CLASSES = {"read", "execution", "write"}
_VALID_CATEGORIES = {"read_only", "controlled_execution", "controlled_write"}
_VALID_EXECUTION_KINDS = {"command_template", "script"}
_VALID_EXECUTION_PLANES = {"worker_runtime", "sandbox_runtime"}
_VALID_MUTATION_DECLARATIONS = {"read_only", "controlled_execution", "controlled_write"}
SCRIPT_BODY_DIGEST_FIELD = "script_body_digest"

# Fields excluded from the digest: lifecycle state must not change the
# identity of the proposed tool content.
_VOLATILE_FIELDS = (
    "status",
    "approval_status",
    "approval_request_id",
    "created_at",
    "updated_at",
    "validated_digest",
    "validation_report_ref",
    "proposal_digest",
)
_PROPOSAL_WRITE_LOCK = threading.RLock()


def _default_data_root() -> Path:
    from agent.config import settings

    return Path(getattr(settings, "data_dir", "data")) / "custom-tools"


def compute_proposal_digest(proposal: dict[str, Any]) -> str:
    payload = {key: value for key, value in proposal.items() if key not in _VOLATILE_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(data)
        tmp.write("\n")
        tmp_name = tmp.name
    try:
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


def _script_digest(data_root: Path, ref: str) -> str:
    script_path = (data_root / ref).resolve()
    store = (data_root / "tool-scripts").resolve()
    if not str(script_path).startswith(str(store) + "/") or not script_path.is_file():
        raise ValueError("script_body_ref_not_readable")
    return hashlib.sha256(script_path.read_bytes()).hexdigest()


def validate_proposal_payload(payload: dict[str, Any]) -> list[str]:
    """Schema validation for ``tool_proposal.v1``; returns error codes."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["proposal_not_an_object"]

    name = str(payload.get("name") or "").strip()
    if not _NAME_RE.match(name):
        errors.append("invalid_name_namespace")
    else:
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        if get_ananta_tool_registry_service().is_known_tool(name):
            errors.append("name_shadows_static_tool")

    for required in ("description", "proposed_by", "source_task_id"):
        if not str(payload.get(required) or "").strip():
            errors.append(f"missing_field:{required}")

    if str(payload.get("risk_class") or "") not in _VALID_RISK_CLASSES:
        errors.append("invalid_risk_class")
    if str(payload.get("category") or "") not in _VALID_CATEGORIES:
        errors.append("invalid_category")
    if str(payload.get("execution_plane") or "") not in _VALID_EXECUTION_PLANES:
        errors.append("missing_or_invalid_execution_plane")
    if str(payload.get("mutation_declaration") or "") not in _VALID_MUTATION_DECLARATIONS:
        errors.append("missing_or_invalid_mutation_declaration")

    argument_schema = payload.get("argument_schema")
    if not isinstance(argument_schema, dict) or not isinstance(argument_schema.get("properties"), dict):
        errors.append("invalid_argument_schema")
        argument_names: set[str] = set()
    else:
        argument_names = set(argument_schema["properties"].keys())

    execution_kind = str(payload.get("execution_kind") or "")
    if execution_kind not in _VALID_EXECUTION_KINDS:
        errors.append("invalid_execution_kind")
    elif execution_kind == "command_template":
        errors.extend(_validate_command_template(payload.get("command_template"), argument_names))
        if payload.get("script_body_ref"):
            errors.append("command_template_with_script_body_ref")
    else:
        ref = str(payload.get("script_body_ref") or "").strip()
        if not ref:
            errors.append("invalid_script_body_ref")
        elif not ref.startswith("tool-scripts/") or ".." in ref:
            errors.append("script_body_ref_outside_store")
        if payload.get("command_template"):
            errors.append("script_with_command_template")

    timeout = payload.get("timeout_seconds")
    if not isinstance(timeout, int) or not (1 <= timeout <= 600):
        errors.append("invalid_timeout_seconds")
    output_max = payload.get("output_max_chars")
    if not isinstance(output_max, int) or not (1 <= output_max <= 100_000):
        errors.append("invalid_output_max_chars")

    for list_field in ("allowed_paths", "denied_paths", "env_allowlist", "intent_aliases", "example_prompts", "negative_examples"):
        value = payload.get(list_field)
        if value is not None and not isinstance(value, list):
            errors.append(f"invalid_list_field:{list_field}")

    tests = payload.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("missing_tests")
    else:
        kinds = {str((case or {}).get("kind") or "") for case in tests if isinstance(case, dict)}
        if "positive" not in kinds:
            errors.append("missing_positive_test")
        if "negative" not in kinds:
            errors.append("missing_negative_test")

    approval_status = str(payload.get("approval_status") or STATUS_PENDING)
    if approval_status not in {STATUS_PENDING, "granted", "denied"}:
        errors.append("invalid_approval_status")
    return errors


def _validate_command_template(template: Any, argument_names: set[str]) -> list[str]:
    """No free shell strings: token list, typed placeholders only."""
    errors: list[str] = []
    if not isinstance(template, list) or not template:
        return ["command_template_must_be_token_list"]
    for token in template:
        if not isinstance(token, str) or not token.strip():
            errors.append("command_template_token_invalid")
            continue
        placeholders = _PLACEHOLDER_RE.findall(token)
        for placeholder in placeholders:
            if placeholder not in argument_names:
                errors.append(f"placeholder_without_argument:{placeholder}")
        static_part = _PLACEHOLDER_RE.sub("", token)
        if any(meta in static_part for meta in _SHELL_METACHARS):
            errors.append("command_template_shell_metacharacter")
    return errors


class CustomToolProposalService:
    """Creates and stores pending ToolProposals (HDE-011).

    The service can never activate anything: proposals start
    ``pending`` and only the promotion service moves them forward.
    """

    def __init__(self, data_root: Path | str | None = None) -> None:
        self._data_root = Path(data_root) if data_root else _default_data_root()

    @property
    def proposals_dir(self) -> Path:
        return self._data_root / "tool-proposals"

    def proposal_path(self, digest: str) -> Path:
        return self.proposals_dir / f"{digest}.json"

    def create_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate, digest and persist one proposal as ``pending``.

        Returns the stored record. Duplicate digests are detected and
        returned instead of re-created. Raises ``ValueError`` with the
        error codes for invalid payloads.
        """
        proposal = dict(payload or {})
        errors = validate_proposal_payload(proposal)
        if errors:
            raise ValueError("invalid_tool_proposal:" + ",".join(sorted(set(errors))))

        proposal["schema"] = TOOL_PROPOSAL_SCHEMA
        # Lifecycle fields are service-owned: a proposer cannot ship a
        # pre-approved or pre-activated proposal.
        proposal["status"] = STATUS_PENDING
        proposal["approval_status"] = STATUS_PENDING
        proposal.pop("approval_request_id", None)
        proposal.pop("validated_digest", None)
        proposal.pop("validation_report_ref", None)
        if str(proposal.get("execution_kind") or "") == "script":
            try:
                proposal[SCRIPT_BODY_DIGEST_FIELD] = _script_digest(self._data_root, str(proposal.get("script_body_ref") or ""))
            except (OSError, ValueError) as exc:
                raise ValueError(f"invalid_tool_proposal:{exc}") from exc
        digest = compute_proposal_digest(proposal)
        proposal["proposal_digest"] = digest

        existing = self.get_proposal(digest)
        if existing is not None:
            return existing

        proposal["created_at"] = time.time()
        with _PROPOSAL_WRITE_LOCK:
            _atomic_write_json(self.proposal_path(digest), proposal)
        return proposal

    def get_proposal(self, digest: str) -> dict[str, Any] | None:
        path = self.proposal_path(str(digest or "").strip())
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def list_proposals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.proposals_dir.is_dir():
            return rows
        for path in sorted(self.proposals_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if status and str(payload.get("status")) != status:
                continue
            rows.append(payload)
        return rows

    def update_proposal(self, digest: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Lifecycle-only updates (status, refs); content stays immutable.

        Content changes must go through a new proposal — they would
        change the digest and silently detach validation/grants
        (HDE-015).
        """
        proposal = self.get_proposal(digest)
        if proposal is None:
            return None
        allowed_keys = {"status", "approval_status", "approval_request_id", "validated_digest", "validation_report_ref", "updated_at"}
        for key, value in (updates or {}).items():
            if key in allowed_keys:
                proposal[key] = value
        if str(proposal.get("status")) not in KNOWN_STATUSES:
            raise ValueError(f"unknown_proposal_status:{proposal.get('status')}")
        proposal["updated_at"] = time.time()
        with _PROPOSAL_WRITE_LOCK:
            _atomic_write_json(self.proposal_path(digest), proposal)
        return proposal


custom_tool_proposal_service: CustomToolProposalService | None = None


def get_custom_tool_proposal_service() -> CustomToolProposalService:
    global custom_tool_proposal_service
    if custom_tool_proposal_service is None:
        custom_tool_proposal_service = CustomToolProposalService()
    return custom_tool_proposal_service
