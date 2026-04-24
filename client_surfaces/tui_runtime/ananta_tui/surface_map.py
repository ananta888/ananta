from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TUI_SECTION_ORDER = (
    "Dashboard",
    "Goals",
    "Tasks",
    "Artifacts",
    "Knowledge",
    "Config",
    "System",
    "Teams",
    "Automation",
    "Audit",
    "Repair",
    "Help",
)

CLASS_TUI_MVP = "tui-mvp"
CLASS_TUI_ADVANCED = "tui-advanced"
CLASS_BROWSER_FALLBACK = "browser-fallback"
CLASS_NOT_TERMINAL = "not-suitable-for-terminal"


@dataclass(frozen=True)
class ApiSurfaceMethod:
    section: str
    method: str
    http_method: str
    endpoint: str
    classification: str
    notes: str


def _entries() -> tuple[ApiSurfaceMethod, ...]:
    return (
        ApiSurfaceMethod(
            "Dashboard",
            "get_dashboard_read_model",
            "GET",
            "/dashboard/read-model",
            CLASS_TUI_MVP,
            "Primary runtime snapshot.",
        ),
        ApiSurfaceMethod(
            "Dashboard",
            "get_assistant_read_model",
            "GET",
            "/assistant/read-model",
            CLASS_TUI_ADVANCED,
            "Optional assistant detail card.",
        ),
        ApiSurfaceMethod("Goals", "list_goals", "GET", "/goals", CLASS_TUI_MVP, "Goal list and summary."),
        ApiSurfaceMethod(
            "Goals", "list_goal_modes", "GET", "/goals/modes", CLASS_TUI_ADVANCED, "Mode-aware goal creation hints."
        ),
        ApiSurfaceMethod("Tasks", "list_tasks", "GET", "/tasks", CLASS_TUI_MVP, "Task board baseline."),
        ApiSurfaceMethod("Artifacts", "list_artifacts", "GET", "/artifacts", CLASS_TUI_MVP, "Artifact list baseline."),
        ApiSurfaceMethod(
            "Knowledge",
            "list_knowledge_collections",
            "GET",
            "/knowledge/collections",
            CLASS_TUI_MVP,
            "Knowledge collection inventory.",
        ),
        ApiSurfaceMethod(
            "Knowledge",
            "list_knowledge_index_profiles",
            "GET",
            "/knowledge/index-profiles",
            CLASS_TUI_ADVANCED,
            "Index profile visibility.",
        ),
        ApiSurfaceMethod(
            "Config", "get_config", "GET", "/config", CLASS_TUI_MVP, "Read-only config summary with redaction."
        ),
        ApiSurfaceMethod(
            "Config", "set_config", "POST", "/config", CLASS_BROWSER_FALLBACK, "Only allowlisted keys from terminal."
        ),
        ApiSurfaceMethod("Config", "list_providers", "GET", "/providers", CLASS_TUI_MVP, "Provider inventory summary."),
        ApiSurfaceMethod(
            "Config",
            "list_provider_catalog",
            "GET",
            "/providers/catalog",
            CLASS_TUI_ADVANCED,
            "Provider catalog metadata.",
        ),
        ApiSurfaceMethod(
            "Config", "get_llm_benchmarks", "GET", "/llm/benchmarks", CLASS_TUI_MVP, "Benchmark summary table."
        ),
        ApiSurfaceMethod(
            "Config",
            "get_llm_benchmarks_config",
            "GET",
            "/llm/benchmarks/config",
            CLASS_TUI_ADVANCED,
            "Benchmark execution configuration.",
        ),
        ApiSurfaceMethod("System", "get_health", "GET", "/health", CLASS_TUI_MVP, "Core service liveness."),
        ApiSurfaceMethod(
            "System",
            "get_system_contracts",
            "GET",
            "/api/system/contracts",
            CLASS_TUI_MVP,
            "Contract and compatibility view.",
        ),
        ApiSurfaceMethod(
            "System", "list_agents", "GET", "/api/system/agents", CLASS_TUI_MVP, "Agent status inventory."
        ),
        ApiSurfaceMethod("System", "get_stats", "GET", "/api/system/stats", CLASS_TUI_MVP, "Current runtime metrics."),
        ApiSurfaceMethod(
            "System",
            "get_stats_history",
            "GET",
            "/api/system/stats/history",
            CLASS_TUI_ADVANCED,
            "Trend snapshot for metrics.",
        ),
        ApiSurfaceMethod("Teams", "list_teams", "GET", "/teams", CLASS_TUI_ADVANCED, "Team visibility baseline."),
        ApiSurfaceMethod(
            "Automation",
            "get_autopilot_status",
            "GET",
            "/tasks/autopilot/status",
            CLASS_TUI_ADVANCED,
            "Autopilot status card.",
        ),
        ApiSurfaceMethod(
            "Automation",
            "get_auto_planner_status",
            "GET",
            "/tasks/auto-planner/status",
            CLASS_TUI_ADVANCED,
            "Auto-planner status card.",
        ),
        ApiSurfaceMethod(
            "Automation",
            "get_triggers_status",
            "GET",
            "/triggers/status",
            CLASS_TUI_ADVANCED,
            "Trigger subsystem status.",
        ),
        ApiSurfaceMethod(
            "Audit",
            "get_audit_logs",
            "GET",
            "/api/system/audit-logs",
            CLASS_TUI_ADVANCED,
            "Paged audit list in terminal.",
        ),
        ApiSurfaceMethod("Repair", "list_repairs", "GET", "/repairs", CLASS_TUI_MVP, "Repair session overview."),
        ApiSurfaceMethod("Repair", "list_approvals", "GET", "/approvals", CLASS_TUI_MVP, "Approval queue visibility."),
        ApiSurfaceMethod(
            "Help",
            "open_in_browser",
            "N/A",
            "browser://deep-link",
            CLASS_BROWSER_FALLBACK,
            "Deep admin and risky flows stay browser-first.",
        ),
        ApiSurfaceMethod(
            "Help",
            "rich_rendering",
            "N/A",
            "terminal://unsupported",
            CLASS_NOT_TERMINAL,
            "Complex rich/binary rendering remains browser-only.",
        ),
    )


def build_hub_api_surface_map() -> dict[str, Any]:
    by_section = {section: [] for section in TUI_SECTION_ORDER}
    for entry in _entries():
        by_section.setdefault(entry.section, []).append(asdict(entry))
    return {
        "schema": "tui_frontend_api_surface_map_v1",
        "sections": list(TUI_SECTION_ORDER),
        "classification_scale": [
            CLASS_TUI_MVP,
            CLASS_TUI_ADVANCED,
            CLASS_BROWSER_FALLBACK,
            CLASS_NOT_TERMINAL,
        ],
        "by_section": by_section,
    }
