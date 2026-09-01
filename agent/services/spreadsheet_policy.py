"""Fail-closed admission policy for spreadsheet documents and actions."""

from __future__ import annotations

from dataclasses import dataclass

from ananta_contracts.spreadsheet_studio import SpreadsheetProposalV1, WorkbookSnapshotV1


@dataclass(frozen=True, slots=True)
class SpreadsheetPolicy:
    enabled: bool
    mode: str
    automatic_promotion_enabled: bool
    max_actions: int = 1_000

    def validate(self) -> None:
        if self.mode not in {"disabled", "mock", "worker"}:
            raise ValueError("spreadsheet_policy_mode_invalid")
        if self.enabled != (self.mode != "disabled"):
            raise ValueError("spreadsheet_policy_enabled_mode_mismatch")
        if not 1 <= self.max_actions <= 1_000:
            raise ValueError("spreadsheet_policy_action_limit_invalid")

    def admit(self, snapshot: WorkbookSnapshotV1, proposal: SpreadsheetProposalV1) -> None:
        if not self.enabled:
            raise PermissionError("spreadsheet_studio_disabled")
        if len(proposal.actions) > self.max_actions:
            raise PermissionError("spreadsheet_action_limit_exceeded")
        sheets = {str(sheet["sheet_id"]): sheet for sheet in snapshot.sheets}
        targets: set[tuple[str, str]] = set()
        for action in proposal.actions:
            sheet_id = str(action["sheet_id"])
            target = (sheet_id, str(action["cell"]))
            if sheet_id not in sheets:
                raise PermissionError("spreadsheet_action_sheet_unknown")
            if sheets[sheet_id]["hidden"]:
                raise PermissionError("spreadsheet_hidden_sheet_write_denied")
            if target in targets:
                raise PermissionError("spreadsheet_action_target_duplicate")
            targets.add(target)


__all__ = ["SpreadsheetPolicy"]
