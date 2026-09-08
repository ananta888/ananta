"""Optional Hub HTTP replay coordination; never a second Worker dispatcher."""

from typing import Protocol

from agent.models.meet_dialog_start import DialogStartClaim, start_fingerprints, start_receipt
from agent.services.meet_contract import MeetError


class StartReceipts(Protocol):
    def claim(self, scope_key: str, request_digest: str) -> DialogStartClaim: ...
    def finish(self, claim: DialogStartClaim, receipt: dict | None = None) -> bool: ...


class DialogStarter(Protocol):
    def start(self, principal, project, payload, parent="") -> dict: ...


class StartAccess(Protocol):
    def require_write_access(self, principal, project, parent=""): ...


class MeetDialogStarts:
    def __init__(self, starter: DialogStarter, receipts: StartReceipts, access: StartAccess):
        self.starter, self.receipts, self.access = starter, receipts, access

    def start(self, principal, project, payload, parent, key):
        # Replaying historical metadata never confers ongoing membership. Even
        # that metadata is not returned to a caller who lost current access.
        self.access.require_write_access(principal, project, parent)
        claim = self.receipts.claim(*start_fingerprints(principal, project, parent, key, payload))
        if not claim.created:
            if claim.state == "completed":
                return claim.receipt(), True
            if claim.state in {"pending", "failed"}:
                raise MeetError("meet_dialog_start_" + claim.state, 409)
            raise MeetError("meet_dialog_start_storage_unavailable", 503)
        try:
            receipt = start_receipt(self.starter.start(principal, project, payload, parent))
        except Exception:
            # Failure may have happened after dispatch. Never release a claim
            # or retry the start. A failed receipt is not a claim about task state.
            try:
                self.receipts.finish(claim)
            except Exception:
                pass  # An uncertain write leaves the durable pending fence.
            raise
        if not self.receipts.finish(claim, receipt):
            raise MeetError("meet_dialog_start_outcome_uncertain", 503)
        return receipt, False
