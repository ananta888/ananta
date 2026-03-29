from __future__ import annotations

from agent.services.autopilot_runtime_service import get_autopilot_runtime_service
from agent.services.trigger_runtime_service import get_trigger_runtime_service


class AutomationSnapshotService:
    """Builds config/dashboard-friendly snapshots for automation runtimes."""

    def build_snapshot(self) -> dict:
        snapshot = {"autopilot": None, "auto_planner": None, "triggers": None}
        try:
            snapshot["autopilot"] = get_autopilot_runtime_service().status()
        except Exception:
            pass
        try:
            from agent.routes.tasks.auto_planner import auto_planner

            snapshot["auto_planner"] = auto_planner.status()
        except Exception:
            pass
        try:
            snapshot["triggers"] = get_trigger_runtime_service().status()
        except Exception:
            pass
        return snapshot


automation_snapshot_service = AutomationSnapshotService()


def get_automation_snapshot_service() -> AutomationSnapshotService:
    return automation_snapshot_service
