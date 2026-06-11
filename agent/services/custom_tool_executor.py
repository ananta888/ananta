"""HDE-016/HDE-018: sandboxed executor for promoted custom tools.

Runs inside the worker runtime (execution plane), never in the hub
control plane: ``LocalProcessWorkerRuntime`` routes ``custom.*`` /
``project.*`` dispatches here. The executor accepts only
``command_template`` token lists with typed arguments or a
``script_body_ref`` inside the approved script store — no unrestricted
shell, no pipes or command substitution beyond what the existing
``ShellCommandPolicy`` explicitly allows (the final rendered command is
analyzed before anything runs).

Enforced boundaries: timeout, cwd inside the workspace, allowed/denied
path globs for path arguments, output cap, env allowlist, and the
mutation gate (HDE-018): a before/after workspace baseline detects
undeclared file changes and blocks the result with a canonical
``workspace_mutation_blocked`` audit event.
"""
from __future__ import annotations

import fnmatch
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.common.audit import (
    AUDIT_WORKSPACE_BASELINE_CREATED,
    AUDIT_WORKSPACE_MUTATION_BLOCKED,
    AUDIT_WORKSPACE_MUTATION_EVALUATED,
    audit_workspace_mutation_event,
)
from agent.services.custom_tool_proposal_service import SCRIPT_BODY_DIGEST_FIELD, _PLACEHOLDER_RE
from agent.services.shell_command_policy import ShellCommandAnalyzer
from agent.services.tools._evidence import EVIDENCE_KIND_TEST_OUTPUT, build_evidence_entry, build_tool_result

_BASELINE_MAX_FILES = 20_000
_FORBIDDEN_VALUE_CHARS = (";", "|", "&", "`", "$(", "${", "<", ">", "\n", "\x00")
_SCRIPT_INTERPRETERS = {"bash": ["bash"], "python3": ["python3"]}
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


@dataclass(frozen=True)
class WorkspaceBaseline:
    files: dict[str, str]
    complete: bool
    scanned_file_count: int
    reason_code: str | None = None


def _blocked(*, tool_name: str, tool_call_id: str, error: str, risk_class: str = "execution", status: str = "rejected") -> dict[str, Any]:
    return build_tool_result(
        tool_name=tool_name, tool_call_id=tool_call_id, status=status, risk_class=risk_class, error=error
    )


def _workspace_baseline(workspace: Path) -> WorkspaceBaseline:
    """Bounded content-hash snapshot for mutation detection (HDE-018)."""
    snapshot: dict[str, str] = {}
    count = 0
    complete = True
    reason_code = None
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        count += 1
        if count > _BASELINE_MAX_FILES:
            complete = False
            reason_code = "baseline_file_limit_exceeded"
            break
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            complete = False
            reason_code = "baseline_read_error"
            continue
        snapshot[str(path.relative_to(workspace))] = digest
    return WorkspaceBaseline(files=snapshot, complete=complete, scanned_file_count=min(count, _BASELINE_MAX_FILES), reason_code=reason_code)


def _changed_paths(before: WorkspaceBaseline, after: WorkspaceBaseline) -> list[str]:
    changed = {path for path, digest in after.files.items() if before.files.get(path) != digest}
    changed |= {path for path in before.files if path not in after.files}
    return sorted(changed)


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


class CustomToolExecutor:
    """Executes one custom tool spec inside its declared boundaries."""

    def __init__(self, data_root: Path | str | None = None) -> None:
        if data_root is None:
            from agent.services.custom_tool_proposal_service import _default_data_root

            data_root = _default_data_root()
        self._data_root = Path(data_root)

    def execute_spec(
        self,
        *,
        spec: dict[str, Any],
        arguments: dict[str, Any] | None,
        workspace_dir: str,
        tool_call_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tool_name = str(spec.get("name") or "custom.unknown")
        risk_class = str(spec.get("risk_class") or "execution")
        cfg = dict(config or {})
        args = dict(arguments or {})

        workspace = Path(str(workspace_dir or "")).resolve()
        if not workspace.is_dir():
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error="invalid_workspace", risk_class=risk_class)

        argument_errors = self._validate_arguments(spec, args, workspace)
        if argument_errors:
            return _blocked(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                error="argument_validation_failed:" + ",".join(argument_errors),
                risk_class=risk_class,
            )

        tokens, render_error = self._render_command(spec, args)
        if render_error:
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error=render_error, risk_class=risk_class)

        # HDE-016: the final rendered command passes the shell policy.
        rendered = " ".join(shlex.quote(token) for token in tokens)
        analysis = ShellCommandAnalyzer().analyze(rendered, cfg)
        if not analysis.allowed:
            return _blocked(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                error=f"shell_policy_blocked:{analysis.denied_reason}",
                risk_class=risk_class,
                status="policy_blocked",
            )

        mutation_declaration = str(spec.get("mutation_declaration") or "read_only")
        task_id = str(cfg.get("task_id") or "") or None
        baseline = _workspace_baseline(workspace)
        audit_workspace_mutation_event(
            AUDIT_WORKSPACE_BASELINE_CREATED,
            task_id=task_id,
            mutation_mode=mutation_declaration,
            baseline_hash=hashlib.sha256(repr(sorted(baseline.files.items())).encode("utf-8")).hexdigest(),
            tool_name=tool_name,
            baseline_complete=baseline.complete,
            scanned_file_count=baseline.scanned_file_count,
            reason_code=baseline.reason_code,
        )
        if not baseline.complete:
            self._audit_incomplete_baseline(
                task_id=task_id,
                mutation_declaration=mutation_declaration,
                baseline=baseline,
                tool_name=tool_name,
            )
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error="workspace_baseline_incomplete", risk_class=risk_class)

        timeout = int(spec.get("timeout_seconds") or 30)
        output_max = int(spec.get("output_max_chars") or 4000)
        env = self._build_env(spec, cfg)
        started = time.time()
        try:
            completed = subprocess.run(
                tokens,
                cwd=str(workspace),
                timeout=timeout,
                env=env,
                capture_output=True,
                text=True,
                shell=False,
            )
            exit_code = completed.returncode
            output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
        except subprocess.TimeoutExpired:
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error=f"timeout_after_{timeout}s", risk_class=risk_class)
        except (OSError, ValueError) as exc:
            return build_tool_result(
                tool_name=tool_name, tool_call_id=tool_call_id, status="error", risk_class=risk_class,
                error=f"custom_tool_execution_failed:{exc}",
            )

        after_baseline = _workspace_baseline(workspace)
        if not after_baseline.complete:
            self._audit_incomplete_baseline(
                task_id=task_id,
                mutation_declaration=mutation_declaration,
                baseline=after_baseline,
                tool_name=tool_name,
            )
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error="workspace_baseline_incomplete", risk_class=risk_class)
        changed = _changed_paths(baseline, after_baseline)
        mutation_error = self._evaluate_mutations(
            spec, mutation_declaration=mutation_declaration, changed=changed, task_id=task_id, tool_name=tool_name
        )
        if mutation_error:
            return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error=mutation_error, risk_class=risk_class)

        truncated_output = output if len(output) <= output_max else output[: max(1, output_max - 12)].rstrip() + "\n[truncated]"
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_TEST_OUTPUT,
            excerpt=truncated_output,
            source=f"custom_tool:{tool_name}",
            max_excerpt_chars=output_max,
        )
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="ok" if exit_code == 0 else "error",
            risk_class=risk_class,
            evidence=[entry],
            data={
                "exit_code": exit_code,
                "duration_ms": int((time.time() - started) * 1000),
                "changed_paths": changed,
                "shell_policy": analysis.as_dict(),
            },
            error=None if exit_code == 0 else f"exit_code_{exit_code}",
        )

    # -- validation & rendering ----------------------------------------------

    @staticmethod
    def _validate_arguments(spec: dict[str, Any], args: dict[str, Any], workspace: Path) -> list[str]:
        errors: list[str] = []
        properties = dict((spec.get("argument_schema") or {}).get("properties") or {})
        for name in args:
            if name not in properties:
                errors.append(f"unknown_argument:{name}")
        type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        path_arguments = set(spec.get("path_arguments") or [])
        allowed_paths = [str(p) for p in (spec.get("allowed_paths") or ["**"])]
        denied_paths = [str(p) for p in (spec.get("denied_paths") or [])]
        for name, value in args.items():
            schema = dict(properties.get(name) or {})
            expected = type_map.get(str(schema.get("type") or "string"))
            if expected and not isinstance(value, expected):
                errors.append(f"argument_type_mismatch:{name}")
                continue
            if isinstance(value, str) and any(ch in value for ch in _FORBIDDEN_VALUE_CHARS):
                errors.append(f"argument_contains_shell_metacharacter:{name}")
                continue
            if name in path_arguments and isinstance(value, str):
                resolved = (workspace / value).resolve()
                try:
                    relative = str(resolved.relative_to(workspace))
                except ValueError:
                    errors.append(f"path_argument_outside_workspace:{name}")
                    continue
                if not _match_any(relative, allowed_paths) or _match_any(relative, denied_paths):
                    errors.append(f"path_argument_not_allowed:{name}")
        return errors

    def _render_command(self, spec: dict[str, Any], args: dict[str, Any]) -> tuple[list[str], str | None]:
        kind = str(spec.get("execution_kind") or "")
        if kind == "command_template":
            tokens: list[str] = []
            for token in spec.get("command_template") or []:
                names = _PLACEHOLDER_RE.findall(str(token))
                rendered = str(token)
                for name in names:
                    if name not in args:
                        return [], f"missing_argument_for_placeholder:{name}"
                    rendered = rendered.replace("{" + name + "}", str(args[name]))
                tokens.append(rendered)
            if not tokens:
                return [], "empty_command_template"
            return tokens, None
        if kind == "script":
            ref = str(spec.get("script_body_ref") or "")
            script_path = (self._data_root / ref).resolve()
            store = (self._data_root / "tool-scripts").resolve()
            if not str(script_path).startswith(str(store) + "/") or not script_path.is_file():
                return [], "script_body_ref_not_in_approved_store"
            expected_digest = str(spec.get(SCRIPT_BODY_DIGEST_FIELD) or "").strip()
            if not expected_digest:
                return [], "script_body_digest_missing"
            try:
                actual_digest = hashlib.sha256(script_path.read_bytes()).hexdigest()
            except OSError:
                return [], "script_body_ref_not_readable"
            if actual_digest != expected_digest:
                return [], "script_body_digest_mismatch"
            interpreter = _SCRIPT_INTERPRETERS.get(str(spec.get("interpreter") or "bash"))
            if interpreter is None:
                return [], "unsupported_script_interpreter"
            tokens = [*interpreter, str(script_path)]
            for name in sorted(args):
                tokens.append(str(args[name]))
            return tokens, None
        return [], f"unsupported_execution_kind:{kind}"

    @staticmethod
    def _build_env(spec: dict[str, Any], cfg: dict[str, Any]) -> dict[str, str]:
        """Minimal base env + explicitly allowlisted names only (HDW-004)."""
        import os

        allowlist = {str(name) for name in (spec.get("env_allowlist") or [])}
        allowlist |= {str(name) for name in (cfg.get("env_allowlist") or [])}
        env = {key: os.environ[key] for key in _BASE_ENV_KEYS if key in os.environ}
        provided = dict(cfg.get("env") or {})
        for name in allowlist:
            if name in provided:
                env[name] = str(provided[name])
            elif name in os.environ:
                env[name] = os.environ[name]
        return env

    @staticmethod
    def _audit_incomplete_baseline(
        *,
        task_id: str | None,
        mutation_declaration: str,
        baseline: WorkspaceBaseline,
        tool_name: str,
    ) -> None:
        audit_workspace_mutation_event(
            AUDIT_WORKSPACE_MUTATION_BLOCKED,
            task_id=task_id,
            mutation_mode=mutation_declaration,
            changed_paths=[],
            policy_decision="blocked",
            blocked_reason="workspace_baseline_incomplete",
            tool_name=tool_name,
            baseline_complete=baseline.complete,
            scanned_file_count=baseline.scanned_file_count,
            reason_code=baseline.reason_code,
        )

    @staticmethod
    def _evaluate_mutations(
        spec: dict[str, Any],
        *,
        mutation_declaration: str,
        changed: list[str],
        task_id: str | None,
        tool_name: str,
    ) -> str | None:
        if not changed:
            audit_workspace_mutation_event(
                AUDIT_WORKSPACE_MUTATION_EVALUATED,
                task_id=task_id,
                mutation_mode=mutation_declaration,
                changed_paths=[],
                policy_decision="allow",
                tool_name=tool_name,
            )
            return None
        if mutation_declaration == "read_only":
            audit_workspace_mutation_event(
                AUDIT_WORKSPACE_MUTATION_BLOCKED,
                task_id=task_id,
                mutation_mode=mutation_declaration,
                changed_paths=changed,
                policy_decision="blocked",
                blocked_reason="read_only_tool_mutated_workspace",
                tool_name=tool_name,
            )
            return "read_only_tool_mutated_workspace"
        allowed_paths = [str(p) for p in (spec.get("allowed_paths") or [])]
        denied_paths = [str(p) for p in (spec.get("denied_paths") or [])]
        violations = [
            path for path in changed
            if not _match_any(path, allowed_paths) or _match_any(path, denied_paths)
        ]
        if violations:
            audit_workspace_mutation_event(
                AUDIT_WORKSPACE_MUTATION_BLOCKED,
                task_id=task_id,
                mutation_mode=mutation_declaration,
                changed_paths=violations,
                policy_decision="blocked",
                blocked_reason="undeclared_workspace_mutation",
                tool_name=tool_name,
            )
            return "undeclared_workspace_mutation"
        audit_workspace_mutation_event(
            AUDIT_WORKSPACE_MUTATION_EVALUATED,
            task_id=task_id,
            mutation_mode=mutation_declaration,
            changed_paths=changed,
            policy_decision="allow",
            tool_name=tool_name,
        )
        return None


def execute_custom_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    workspace_dir: str,
    tool_call_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Worker-runtime entry point: resolve the active record, execute, track usage."""
    from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

    registry = get_dynamic_tool_registry_service()
    record = registry.get_active_tool(tool_name)
    if record is None:
        return _blocked(tool_name=tool_name, tool_call_id=tool_call_id, error="custom_tool_not_active")
    result = CustomToolExecutor().execute_spec(
        spec=dict(record.get("spec") or {}),
        arguments=arguments,
        workspace_dir=workspace_dir,
        tool_call_id=tool_call_id,
        config=config,
    )
    try:
        registry.record_usage(
            tool_name,
            success=str(result.get("status")) == "ok",
            failure_reason=str(result.get("error") or "") or None,
        )
    except Exception:
        pass
    return result
