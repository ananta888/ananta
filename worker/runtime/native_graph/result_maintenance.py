"""Finite Worker-local result housekeeping; no scheduling, Task or Hub ownership."""

import argparse
import json
import os

from worker.runtime.native_graph.result_store import SQLiteNativeResultStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scrub one batch of expired private Worker results.")
    parser.add_argument("--batch-size", type=int, default=100)
    options = parser.parse_args(argv)
    path = os.environ.get("ANANTA_NATIVE_RESULT_STORE_PATH")
    try:
        if not 1 <= options.batch_size <= 1000:
            raise ValueError("native_result_store_expiry_batch_invalid")
        if not path:
            raise ValueError("native_result_store_configuration_required")
        # Constructor housekeeping is disabled here so the reported finite batch
        # covers every mutation performed by this invocation.
        store = SQLiteNativeResultStore(path, expire_on_open=False)
        scrubbed = store.expire_results(batch_size=options.batch_size)
    except Exception:
        print(json.dumps({"schema": "ananta.native-result-maintenance.v1", "status": "blocked",
                          "reason_code": "native_result_maintenance_unavailable"}))
        return 1
    print(json.dumps({"schema": "ananta.native-result-maintenance.v1", "status": "completed",
                      "scrubbed_results": scrubbed, "replay_tombstones_preserved": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
