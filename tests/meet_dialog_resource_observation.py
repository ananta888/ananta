"""Opt-in numeric cgroup samples; no instrumentation loop inside a Worker."""

import time


class DialogResourceObservation:
    def __init__(self, enabled, transports, *, clock=time.monotonic, wait=time.sleep):
        self.enabled, self.transports = enabled, tuple(transports)
        self.clock, self.wait = clock, wait
        self.samples = []

    def capture(self, expected_active, *, settle=False, unavailable=()):
        if not self.enabled:
            return
        deadline = self.clock() + (5 if settle else 0)
        while True:
            rows = [
                None if index in unavailable else transport.observe_dialog_resources()
                for index, transport in enumerate(self.transports)
            ]
            if all(row is None or row["slots"]["active"] == expected_active for row in rows):
                break
            if not settle or self.clock() >= deadline:
                raise AssertionError("test_dialog_resource_occupancy_mismatch")
            self.wait(0.1)
        for index, row in enumerate(rows):
            if row is None:
                continue
            assert row["slots"]["capacity"] == 2
            counters = row["cgroup"]
            assert all(type(value) is int for value in counters.values()), "reference counters unavailable"
            assert counters["memory_limit_bytes"] == 1024**3, "exact owned non-GPU container memory quota"
            assert 0 < counters["memory_bytes"] < counters["memory_limit_bytes"]
            assert 0 < counters["pids"] <= 256 and counters["cpu_usage_us"] >= 0
            if self.samples and self.samples[-1][index] is not None:
                previous = self.samples[-1][index]
                elapsed = row["sampled_monotonic_us"] - previous["sampled_monotonic_us"]
                used = counters["cpu_usage_us"] - previous["cgroup"]["cpu_usage_us"]
                assert elapsed > 0 and used >= 0
                assert used / elapsed <= 2.25, "two-core quota plus bounded sampling/burst margin"
        self.samples.append(rows)

    def record(self, record_property):
        if self.enabled:
            record_property(
                "packaged_dialog_resource_samples",
                {
                    "synthetic_policy": True,
                    "production_release_evidence": False,
                    "gpu_utilization": "unverified",
                    "sampling": "startup-active-terminal-not-continuous",
                    "samples": [
                        [
                            None
                            if row is None
                            else {key: row[key] for key in ("slots", "cgroup", "sampled_monotonic_us")}
                            for row in rows
                        ]
                        for rows in self.samples
                    ],
                },
            )
