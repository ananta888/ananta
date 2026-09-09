"""Irreversible local quality fence; no grants, source opening or task decisions."""

import time

from ananta_contracts.meet_media_timing import MAX_DRIFT_US, SOURCES, validate_media_timing


class MediaTimingGate:
    def __init__(self, epoch, sources, *, clock=time.monotonic):
        if (
            type(epoch) is not int
            or not 1 <= epoch <= 4096
            or not isinstance(sources, (set, frozenset))
            or not sources <= SOURCES
        ):
            raise ValueError("meet_media_timing_gate_invalid")
        self.epoch, self.sources, self.clock = epoch, frozenset(sources), clock
        self.closed = False
        self.first_at = self.first_browser = self.last_at = self.last_browser = None
        self.previous = {}
        self.retired = {}

    def accept(self, value):
        if self.closed:
            raise ValueError("meet_media_timing_closed")
        try:
            result = validate_media_timing(value)
            now = self.clock()
            if not isinstance(now, (float, int)) or isinstance(now, bool) or not 0 <= now < float("inf"):
                raise ValueError("meet_media_timing_clock_invalid")
            if result["epoch"] != self.epoch or not set(result["sources"]) <= self.sources:
                raise ValueError("meet_media_timing_scope_changed")
            browser = result["now_us"]
            if self.first_at is None:
                self.first_at, self.first_browser = now, browser
            elif (
                now < self.last_at
                or browser < self.last_browser
                or abs((now - self.first_at) * 1_000_000 - (browser - self.first_browser)) > MAX_DRIFT_US
            ):
                raise ValueError("meet_media_timing_clock_changed")
            for name, previous in self.previous.items():
                if name not in result["sources"]:
                    self.retired[name] = previous["generation"]
            for name, source in result["sources"].items():
                self._advance(name, source)
            self.last_at, self.last_browser = now, browser
            # Retain private copies: mutation of an accepted result cannot
            # rewrite the next comparison, origin or retired generation.
            self.previous = {name: dict(source) for name, source in result["sources"].items()}
            return result
        except Exception:
            self.close()
            raise

    def _advance(self, name, source):
        if source["state"] == "failed":
            raise ValueError("meet_media_timing_source_failed")
        previous = self.previous.get(name)
        generation = source["generation"]
        if generation <= self.retired.get(name, 0) or previous and generation < previous["generation"]:
            raise ValueError("meet_media_timing_generation_stale")
        if previous is None or generation > previous["generation"]:
            return
        if (
            any(source[key] != previous[key] for key in ("measurement", "started_at_us", "origin_position_us"))
            or any(source[key] < previous[key] for key in ("position_at_us", "observed_at_us"))
            or source["position_us"] is not None
            and source["position_us"] < previous["position_us"]
            or previous["state"] == "held"
            and source["state"] != "held"
        ):
            raise ValueError("meet_media_timing_source_changed")

    def close(self):
        self.closed = True
