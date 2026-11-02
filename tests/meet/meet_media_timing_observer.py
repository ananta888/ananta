"""Content-free failure observations without changing the real quality fence."""

from ananta_contracts.meet_media_timing import validate_media_timing
from worker.meet_media.media_timing_gate import MediaTimingGate


def observe_media_timing_failure(monkeypatch, record_property):
    accept = MediaTimingGate.accept
    recorded = False

    def observed(gate, value):
        nonlocal recorded
        try:
            return accept(gate, value)
        except Exception:
            if not recorded:
                recorded = True
                try:
                    observation = validate_media_timing(value)
                except (TypeError, ValueError):
                    observation = {"state": "invalid_snapshot"}
                record_property("dialog_media_timing_failure", observation)
            raise

    monkeypatch.setattr(MediaTimingGate, "accept", observed)
