"""Runtime policy that prevents newly generated source monoliths.

The service is a post-mutation quality guard. It does not replace the
workspace mutation security policy; callers use it after path/scope
checks to decide whether a source-size change is ok, warning,
follow-up-required, or blocked.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CATEGORY_PRODUCTION_SOURCE = "production_source"
CATEGORY_FACADE_OR_ROUTES = "facade_or_routes"
CATEGORY_TESTS = "tests"
CATEGORY_GENERATED = "generated"
CATEGORY_DATA_SCHEMA_CONFIG = "data_schema_config"
CATEGORY_EXCLUDED = "excluded"

DECISION_OK = "ok"
DECISION_WARNING = "warning"
DECISION_FOLLOWUP_REQUIRED = "followup_required"
DECISION_BLOCKED = "blocked"

ACTION_ALLOW = "allow"
ACTION_WARN = "warn"
ACTION_REQUIRE_FOLLOWUP = "require_followup"
ACTION_BLOCK = "block"

REASON_NEW_FILE_OVER_HARD_LIMIT = "new_file_over_hard_limit"
REASON_CROSSED_HARD_LIMIT = "crossed_hard_limit"
REASON_EXISTING_OVER_LIMIT_GREW = "existing_over_limit_grew"
REASON_OVER_WARNING_THRESHOLD = "over_warning_threshold"
REASON_CATEGORY_EXCLUDED = "category_excluded"
REASON_GENERATED_ALLOWED_WITH_REASON = "generated_allowed_with_reason"
REASON_UNREADABLE_FILE = "unreadable_file"
REASON_OK = "ok"

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".go", ".rs", ".sh"}
EXCLUDED_DIR_PARTS = {
    ".git",
    ".ananta",
    ".venv",
    "venv",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    "out",
    "artifacts",
    "data",
    "project-workspaces",
    "autoimport-state",
}
EXCLUDED_PREFIXES = (
    ".claude/worktrees/",
    "frontend-angular/dist/",
    "client_surfaces/vscode_extension/out/",
)
GENERATED_PATTERNS = (
    "*.min.js",
    "*.generated.*",
    "*_pb2.py",
    "*_pb2_grpc.py",
)
TEST_PATH_MARKERS = ("/tests/", "tests/", "/test/", "test/")
TEST_NAME_PATTERNS = ("test_*.py", "*_test.py", "*.spec.ts", "*.test.ts", "*.spec.tsx", "*.test.tsx")
FACADE_OR_ROUTE_MARKERS = ("/routes/", "routes/", "/api/", "api/", "facade", "router")
DATA_SCHEMA_CONFIG_MARKERS = (
    "/schemas/",
    "schemas/",
    "/config/",
    "config/",
    "/fixtures/",
    "fixtures/",
)
DATA_SCHEMA_CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini"}


DEFAULT_POLICY_CONFIG: dict[str, Any] = {
    "enabled": False,
    "mode": "warn",
    "create_followup_todo": False,
    "max_report_files": 50,
    "categories": {
        CATEGORY_PRODUCTION_SOURCE: {
            "target_lines": 800,
            "warn_after_lines": 600,
            "hard_max_lines": 1000,
            "new_over_hard_action": ACTION_BLOCK,
            "cross_hard_action": ACTION_REQUIRE_FOLLOWUP,
            "existing_over_hard_growth_action": ACTION_REQUIRE_FOLLOWUP,
            "existing_over_hard_shrink_action": ACTION_WARN,
            "warn_action": ACTION_WARN,
        },
        CATEGORY_FACADE_OR_ROUTES: {
            "target_lines": 900,
            "warn_after_lines": 700,
            "hard_max_lines": 1000,
            "new_over_hard_action": ACTION_REQUIRE_FOLLOWUP,
            "cross_hard_action": ACTION_REQUIRE_FOLLOWUP,
            "existing_over_hard_growth_action": ACTION_REQUIRE_FOLLOWUP,
            "existing_over_hard_shrink_action": ACTION_WARN,
            "warn_action": ACTION_WARN,
        },
        CATEGORY_TESTS: {
            "target_lines": 1000,
            "warn_after_lines": 1000,
            "hard_max_lines": 1500,
            "new_over_hard_action": ACTION_WARN,
            "cross_hard_action": ACTION_WARN,
            "existing_over_hard_growth_action": ACTION_REQUIRE_FOLLOWUP,
            "existing_over_hard_shrink_action": ACTION_WARN,
            "warn_action": ACTION_WARN,
        },
        CATEGORY_DATA_SCHEMA_CONFIG: {
            "target_lines": 1200,
            "warn_after_lines": 1200,
            "hard_max_lines": 1500,
            "new_over_hard_action": ACTION_WARN,
            "cross_hard_action": ACTION_WARN,
            "existing_over_hard_growth_action": ACTION_WARN,
            "existing_over_hard_shrink_action": ACTION_WARN,
            "warn_action": ACTION_WARN,
        },
        CATEGORY_GENERATED: {
            "target_lines": 1200,
            "warn_after_lines": 1200,
            "hard_max_lines": 1500,
            "new_over_hard_action": ACTION_WARN,
            "cross_hard_action": ACTION_WARN,
            "existing_over_hard_growth_action": ACTION_WARN,
            "existing_over_hard_shrink_action": ACTION_WARN,
            "warn_action": ACTION_WARN,
        },
    },
}


@dataclass(frozen=True)
class LineCountResult:
    line_count: int
    file_size_bytes: int
    unreadable_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_count": self.line_count,
            "file_size_bytes": self.file_size_bytes,
            "unreadable_reason": self.unreadable_reason,
        }


@dataclass(frozen=True)
class SourceLinePolicyFileResult:
    path: str
    category: str
    before_lines: int | None
    after_lines: int | None
    decision: str
    action: str
    reason_code: str
    threshold: int | None = None
    warning_threshold: int | None = None
    unreadable_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "before_lines": self.before_lines,
            "after_lines": self.after_lines,
            "decision": self.decision,
            "action": self.action,
            "reason_code": self.reason_code,
            "threshold": self.threshold,
            "warning_threshold": self.warning_threshold,
            "unreadable_reason": self.unreadable_reason,
        }


@dataclass(frozen=True)
class SourceLinePolicyResult:
    status: str
    enabled: bool
    file_results: list[SourceLinePolicyFileResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    followup_todos: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        counts = {DECISION_OK: 0, DECISION_WARNING: 0, DECISION_FOLLOWUP_REQUIRED: 0, DECISION_BLOCKED: 0}
        for row in self.file_results:
            counts[row.decision] = int(counts.get(row.decision) or 0) + 1
        return {
            "schema": "generated_source_line_policy_result.v1",
            "enabled": self.enabled,
            "status": self.status,
            "summary": {
                "total_files": len(self.file_results),
                "ok": counts[DECISION_OK],
                "warning": counts[DECISION_WARNING],
                "followup_required": counts[DECISION_FOLLOWUP_REQUIRED],
                "blocked": counts[DECISION_BLOCKED],
            },
            "file_results": [row.as_dict() for row in self.file_results],
            "warnings": list(self.warnings),
            "followup_todos": list(self.followup_todos),
        }


def normalize_generated_source_line_policy_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_POLICY_CONFIG))
    incoming = dict(raw or {})
    for key, value in incoming.items():
        if key == "categories" and isinstance(value, dict):
            for category, category_cfg in value.items():
                if isinstance(category_cfg, dict):
                    base = dict(cfg["categories"].get(category) or {})
                    base.update(category_cfg)
                    cfg["categories"][category] = base
        else:
            cfg[key] = value
    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["mode"] = str(cfg.get("mode") or "warn").strip().lower()
    if cfg["mode"] not in {"off", "warn", "followup_required", "block"}:
        cfg["mode"] = "warn"
    cfg["create_followup_todo"] = bool(cfg.get("create_followup_todo", False))
    cfg["max_report_files"] = max(1, min(int(cfg.get("max_report_files") or 50), 500))
    return cfg


def extract_policy_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    raw = cfg.get("generated_source_line_policy")
    if raw is None and isinstance(cfg.get("agent_config"), dict):
        raw = dict(cfg.get("agent_config") or {}).get("generated_source_line_policy")
    if raw is None:
        raw = {}
    return normalize_generated_source_line_policy_config(raw if isinstance(raw, dict) else {})


def _normalize_rel(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip().lstrip("/")


def _is_excluded(rel: str) -> tuple[bool, str | None]:
    if any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True, "excluded_prefix"
    parts = set(Path(rel).parts)
    for part in sorted(EXCLUDED_DIR_PARTS):
        if part in parts:
            return True, f"excluded_dir:{part}"
    return False, None


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


class GeneratedSourceLinePolicyService:
    def classify_path(self, rel_path: str, *, extensions: set[str] | None = None) -> dict[str, Any]:
        rel = _normalize_rel(rel_path)
        extension = Path(rel).suffix.lower()
        ext_set = extensions or SOURCE_EXTENSIONS
        excluded, reason = _is_excluded(rel)
        if excluded:
            return {"path": rel, "extension": extension, "category": CATEGORY_EXCLUDED, "reason": reason}
        if extension not in ext_set and extension not in DATA_SCHEMA_CONFIG_EXTENSIONS:
            return {
                "path": rel,
                "extension": extension,
                "category": CATEGORY_EXCLUDED,
                "reason": "extension_not_counted",
            }
        name = Path(rel).name
        if _matches_any(name, GENERATED_PATTERNS):
            return {"path": rel, "extension": extension, "category": CATEGORY_GENERATED, "reason": f"generated_pattern:{name}"}
        normalized = f"/{rel}"
        if any(marker in normalized for marker in TEST_PATH_MARKERS) or _matches_any(name, TEST_NAME_PATTERNS):
            return {"path": rel, "extension": extension, "category": CATEGORY_TESTS, "reason": "test_path_or_name"}
        if extension in DATA_SCHEMA_CONFIG_EXTENSIONS or any(marker in normalized for marker in DATA_SCHEMA_CONFIG_MARKERS):
            return {
                "path": rel,
                "extension": extension,
                "category": CATEGORY_DATA_SCHEMA_CONFIG,
                "reason": "data_schema_config_path_or_extension",
            }
        if any(marker in normalized for marker in FACADE_OR_ROUTE_MARKERS):
            return {
                "path": rel,
                "extension": extension,
                "category": CATEGORY_FACADE_OR_ROUTES,
                "reason": "facade_or_route_path",
            }
        return {"path": rel, "extension": extension, "category": CATEGORY_PRODUCTION_SOURCE, "reason": "source_extension"}

    def count_file_lines(self, path: Path) -> LineCountResult:
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                return LineCountResult(line_count=sum(1 for _ in handle), file_size_bytes=size)
        except OSError as exc:
            return LineCountResult(line_count=0, file_size_bytes=0, unreadable_reason=exc.__class__.__name__)

    def build_baseline(self, *, workspace_dir: str | Path, changed_rel_paths: list[str] | None) -> dict[str, int | None]:
        workspace = Path(workspace_dir).resolve()
        baseline: dict[str, int | None] = {}
        for rel in list(changed_rel_paths or []):
            normalized = _normalize_rel(rel)
            target = (workspace / normalized).resolve()
            try:
                if not str(target).startswith(str(workspace)) or not target.exists():
                    baseline[normalized] = None
                else:
                    baseline[normalized] = self.count_file_lines(target).line_count
            except OSError:
                baseline[normalized] = None
        return baseline

    def evaluate_changed_files(
        self,
        *,
        workspace_dir: str | Path,
        changed_rel_paths: list[str] | None,
        cfg: dict[str, Any] | None,
        baseline: dict[str, int | None] | None = None,
        context: dict[str, Any] | None = None,
    ) -> SourceLinePolicyResult:
        policy_cfg = normalize_generated_source_line_policy_config(cfg)
        if not policy_cfg.get("enabled") or policy_cfg.get("mode") == "off":
            return SourceLinePolicyResult(status=DECISION_OK, enabled=False)
        workspace = Path(workspace_dir).resolve()
        rows: list[SourceLinePolicyFileResult] = []
        warnings: list[str] = []
        followups: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_rel in list(changed_rel_paths or []):
            rel = _normalize_rel(raw_rel)
            if not rel or rel in seen:
                continue
            seen.add(rel)
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                rows.append(
                    SourceLinePolicyFileResult(
                        path=rel,
                        category=CATEGORY_EXCLUDED,
                        before_lines=None,
                        after_lines=None,
                        decision=DECISION_BLOCKED,
                        action=ACTION_BLOCK,
                        reason_code="invalid_workspace_relative_path",
                    )
                )
                continue
            classification = self.classify_path(rel)
            category = str(classification["category"])
            if category == CATEGORY_EXCLUDED:
                rows.append(
                    SourceLinePolicyFileResult(
                        path=rel,
                        category=category,
                        before_lines=(baseline or {}).get(rel),
                        after_lines=None,
                        decision=DECISION_OK,
                        action=ACTION_ALLOW,
                        reason_code=REASON_CATEGORY_EXCLUDED,
                    )
                )
                continue
            target = (workspace / rel).resolve()
            try:
                if not str(target).startswith(str(workspace)):
                    raise OSError("outside_workspace_root")
                count = self.count_file_lines(target)
            except OSError as exc:
                count = LineCountResult(0, 0, exc.__class__.__name__)
            before_lines = (baseline or {}).get(rel)
            row = self._evaluate_one(
                rel=rel,
                category=category,
                before_lines=before_lines,
                after_count=count,
                policy_cfg=policy_cfg,
            )
            rows.append(row)
            if row.decision == DECISION_FOLLOWUP_REQUIRED and policy_cfg.get("create_followup_todo"):
                followups.append(self._build_followup(row, context=context))
            if row.unreadable_reason:
                warnings.append(f"{rel}:{row.unreadable_reason}")
        status = self._aggregate_status(rows)
        self._record_metrics(rows)
        persisted_followups = self._persist_followups(
            workspace_dir=workspace,
            followups=self._dedupe_followups(followups),
            enabled=bool(policy_cfg.get("create_followup_todo")),
        )
        return SourceLinePolicyResult(
            status=status,
            enabled=True,
            file_results=rows[: int(policy_cfg.get("max_report_files") or 50)],
            warnings=warnings,
            followup_todos=persisted_followups,
        )

    def _evaluate_one(
        self,
        *,
        rel: str,
        category: str,
        before_lines: int | None,
        after_count: LineCountResult,
        policy_cfg: dict[str, Any],
    ) -> SourceLinePolicyFileResult:
        category_cfg = dict((policy_cfg.get("categories") or {}).get(category) or {})
        hard = int(category_cfg.get("hard_max_lines") or 1000)
        warn = int(category_cfg.get("warn_after_lines") or hard)
        after_lines = after_count.line_count
        if after_count.unreadable_reason:
            return self._file_result(
                rel,
                category,
                before_lines,
                after_lines,
                ACTION_WARN,
                REASON_UNREADABLE_FILE,
                hard,
                warn,
                after_count,
                mode=str(policy_cfg.get("mode") or "block"),
            )
        if category == CATEGORY_GENERATED and after_lines > warn:
            return self._file_result(
                rel,
                category,
                before_lines,
                after_lines,
                ACTION_WARN,
                REASON_GENERATED_ALLOWED_WITH_REASON,
                hard,
                warn,
                after_count,
                mode=str(policy_cfg.get("mode") or "block"),
            )
        action = ACTION_ALLOW
        reason = REASON_OK
        if before_lines is None and after_lines > hard:
            action = str(category_cfg.get("new_over_hard_action") or ACTION_BLOCK)
            reason = REASON_NEW_FILE_OVER_HARD_LIMIT
        elif before_lines is not None and before_lines <= hard < after_lines:
            action = str(category_cfg.get("cross_hard_action") or ACTION_REQUIRE_FOLLOWUP)
            reason = REASON_CROSSED_HARD_LIMIT
        elif before_lines is not None and before_lines > hard and after_lines > before_lines:
            action = str(category_cfg.get("existing_over_hard_growth_action") or ACTION_REQUIRE_FOLLOWUP)
            reason = REASON_EXISTING_OVER_LIMIT_GREW
        elif before_lines is not None and before_lines > hard and after_lines <= before_lines:
            action = str(category_cfg.get("existing_over_hard_shrink_action") or ACTION_WARN)
            reason = REASON_EXISTING_OVER_LIMIT_GREW if after_lines > hard else REASON_OVER_WARNING_THRESHOLD
        elif after_lines > warn:
            action = str(category_cfg.get("warn_action") or ACTION_WARN)
            reason = REASON_OVER_WARNING_THRESHOLD
        return self._file_result(
            rel,
            category,
            before_lines,
            after_lines,
            action,
            reason,
            hard,
            warn,
            after_count,
            mode=str(policy_cfg.get("mode") or "block"),
        )

    def _file_result(
        self,
        rel: str,
        category: str,
        before_lines: int | None,
        after_lines: int,
        action: str,
        reason: str,
        hard: int | None,
        warn: int | None,
        count: LineCountResult,
        mode: str,
    ) -> SourceLinePolicyFileResult:
        effective_action = self._apply_mode(action, policy_cfg_mode=mode)
        return SourceLinePolicyFileResult(
            path=rel,
            category=category,
            before_lines=before_lines,
            after_lines=after_lines,
            decision=self._action_to_decision(effective_action),
            action=effective_action,
            reason_code=reason,
            threshold=hard,
            warning_threshold=warn,
            unreadable_reason=count.unreadable_reason,
        )

    @staticmethod
    def _apply_mode(action: str, policy_cfg_mode: str | None) -> str:
        mode = str(policy_cfg_mode or "block").strip().lower()
        if mode == "warn" and action in {ACTION_REQUIRE_FOLLOWUP, ACTION_BLOCK}:
            return ACTION_WARN
        if mode == "followup_required" and action == ACTION_BLOCK:
            return ACTION_REQUIRE_FOLLOWUP
        return action

    @staticmethod
    def _action_to_decision(action: str) -> str:
        if action == ACTION_BLOCK:
            return DECISION_BLOCKED
        if action == ACTION_REQUIRE_FOLLOWUP:
            return DECISION_FOLLOWUP_REQUIRED
        if action == ACTION_WARN:
            return DECISION_WARNING
        return DECISION_OK

    @staticmethod
    def _aggregate_status(rows: list[SourceLinePolicyFileResult]) -> str:
        decisions = {row.decision for row in rows}
        if DECISION_BLOCKED in decisions:
            return DECISION_BLOCKED
        if DECISION_FOLLOWUP_REQUIRED in decisions:
            return DECISION_FOLLOWUP_REQUIRED
        if DECISION_WARNING in decisions:
            return DECISION_WARNING
        return DECISION_OK

    @staticmethod
    def _build_followup(row: SourceLinePolicyFileResult, *, context: dict[str, Any] | None) -> dict[str, Any]:
        ctx = dict(context or {})
        return {
            "dedupe_key": f"generated-source-line-policy:{row.path}:{row.reason_code}:{row.threshold}",
            "track": "generated-source-line-policy",
            "path": row.path,
            "after_lines": row.after_lines,
            "threshold": row.threshold,
            "reason_code": row.reason_code,
            "task_id": ctx.get("task_id"),
            "goal_id": ctx.get("goal_id"),
            "suggested_split_direction": "Extract cohesive responsibilities into smaller modules behind stable interfaces.",
        }

    @staticmethod
    def _dedupe_followups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("dedupe_key") or "")
            if key and key not in by_key:
                by_key[key] = row
        return list(by_key.values())

    @staticmethod
    def _persist_followups(*, workspace_dir: Path, followups: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
        if not enabled or not followups:
            return followups
        path = workspace_dir / ".ananta" / "source-line-followups.json"
        existing: list[dict[str, Any]] = []
        try:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                existing = list(payload.get("followups") or []) if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            existing = []
        by_key: dict[str, dict[str, Any]] = {
            str(row.get("dedupe_key") or ""): dict(row)
            for row in existing
            if isinstance(row, dict) and str(row.get("dedupe_key") or "")
        }
        for row in followups:
            key = str(row.get("dedupe_key") or "")
            if key and key not in by_key:
                by_key[key] = dict(row)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema": "generated_source_line_policy_followups.v1",
                        "followups": list(by_key.values()),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        except OSError:
            return followups
        return followups

    @staticmethod
    def _record_metrics(rows: list[SourceLinePolicyFileResult]) -> None:
        try:
            from agent.services.task_execution_metrics import record_source_line_policy_metric

            for row in rows:
                record_source_line_policy_metric(row.decision, category=row.category, reason_code=row.reason_code)
        except Exception:
            return


generated_source_line_policy_service = GeneratedSourceLinePolicyService()


def get_generated_source_line_policy_service() -> GeneratedSourceLinePolicyService:
    return generated_source_line_policy_service
