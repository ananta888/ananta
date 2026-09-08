"""Optional operator allowlist, separate from admission and dispatch decisions."""

import json

from agent.services.meet_dialog_publishers import MeetDialogPublishers
from agent.services.meet_media_transport import HttpMediaWorker


def configured_dialog_workers(raw, default_worker, rows):
    if raw is None:
        return None, None
    if not isinstance(raw, str) or len(raw.encode()) > 8192:
        raise ValueError("meet_dialog_publishers_invalid")
    try:
        endpoints = json.loads(raw)
        if (
            not isinstance(endpoints, list)
            or len(endpoints) > 8
            or any(
                not isinstance(item, str) or not item or len(item) > 512 or any(c.isspace() for c in item)
                for item in endpoints
            )
            or len(set(endpoints)) != len(endpoints)
        ):
            raise ValueError()
        workers = {default_worker.publisher_url: default_worker}
        for endpoint in endpoints:
            worker = HttpMediaWorker(endpoint, default_worker.key)
            if worker.publisher_url in workers:
                if endpoint != default_worker.endpoint:
                    raise ValueError()
                continue
            workers[worker.publisher_url] = worker
        selected = MeetDialogPublishers(rows, list(workers), default_worker.publisher_url)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("meet_dialog_publishers_invalid") from None
    return selected, workers
