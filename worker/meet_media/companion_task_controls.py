"""Current Hub source checkpoints for one assignment; no Worker policy issuance."""

import time
from copy import deepcopy
from dataclasses import dataclass

from worker.meet_media.dialog_control_exchange import DialogControlExchange


class CompanionControlLost(BaseException):
    """Abort owned execution, bypassing per-message fallback/reply handlers."""


class CompanionSourceInterrupted(CompanionControlLost):
    """One Hub-controlled source changed; unrelated sources remain usable."""


@dataclass(frozen=True)
class CompanionSourceCheckpoint:
    source: str
    revision: int
    since: int
    owner: object


class CompanionTaskControls:
    """Apply signed observations and fence old source operations independently.

    The caller owns browser/media execution. The single-flight background read
    uses the existing exchange, never calls browser ports and schedules no work.
    """

    def __init__(self, client, *, clock=time.monotonic, pool=None):
        self.client, self.clock = client, clock
        self.exchange = DialogControlExchange(None, None, clock=clock, pool=pool, read=client.read)
        self.state = None
        self.fresh_until = 0
        self.closed = False
        self.owner = object()  # Local operation ownership, not an evidence identity.

    def step(self):
        try:
            if self.closed or self.clock() >= self.client.deadline:
                raise ValueError()
            if self.state is not None and self.clock() >= self.fresh_until:
                raise ValueError()
            observed = self.exchange.poll()
            if observed is None:
                return None
            # A slow request must consume freshness, not add another 2.5 s
            # after its response. The assignment deadline remains fixed.
            fresh_until = min(self.exchange.started + 2.5, self.client.deadline)
            if self.clock() >= fresh_until:
                raise ValueError()
            current = observed["controls"]
            if self.state is not None:
                previous = self.state["controls"]
                if current["revision"] < previous["revision"] or current.keys() != previous.keys():
                    raise ValueError()
                for source in current.keys() - {"revision"}:
                    before, after = previous[source], current[source]
                    if (after["revision"] < before["revision"] or after["since"] < before["since"]
                            or after["revision"] == before["revision"] and after != before
                            or current["revision"] == previous["revision"] and after != before):
                        raise ValueError()
            self.state = deepcopy(observed)
            self.fresh_until = fresh_until
            return deepcopy(observed)
        except Exception:
            self.close()
            raise CompanionControlLost("meet_companion_task_control_lost") from None

    def checkpoint(self, source):
        row = self._current(source)
        if not row["enabled"]:
            raise CompanionSourceInterrupted("meet_companion_source_disabled")
        return CompanionSourceCheckpoint(source, row["revision"], row["since"], self.owner)

    def require_current(self, checkpoint):
        if not isinstance(checkpoint, CompanionSourceCheckpoint) or checkpoint.owner is not self.owner:
            raise CompanionControlLost("meet_companion_source_checkpoint_invalid")
        row = self._current(checkpoint.source)
        if not row["enabled"] or (row["revision"], row["since"]) != (checkpoint.revision, checkpoint.since):
            raise CompanionSourceInterrupted("meet_companion_source_changed")

    def _current(self, source):
        if (self.closed or self.state is None or self.clock() >= min(self.fresh_until, self.client.deadline)
                or source not in {"chat", "speech", "avatar"} or source not in self.state["controls"]):
            raise CompanionControlLost("meet_companion_source_unavailable")
        return self.state["controls"][source]

    def close(self):
        self.closed = True
        self.state = None
        self.fresh_until = 0
        self.exchange.close()
