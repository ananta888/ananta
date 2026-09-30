"""Compatibility re-export of the external Voice deletion ledger adapter.

The ledger is a pure file-persistence adapter (segmented, HMAC-chained JSONL
under a file lock) and therefore lives in
:mod:`agent.repositories.voice_deletion_ledger`. Existing imports from this
module keep resolving to the very same objects.
"""

from __future__ import annotations

from agent.repositories.voice_deletion_ledger import (
    VoiceDeletionLedger,
    VoiceDeletionLedgerClaim,
    VoiceDeletionLedgerRecord,
)

__all__ = ["VoiceDeletionLedger", "VoiceDeletionLedgerClaim", "VoiceDeletionLedgerRecord"]
