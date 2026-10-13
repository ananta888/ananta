"""One actual asynchronous Hub tick so AND-join assertions can observe siblings."""

import os
from concurrent.futures import ThreadPoolExecutor

from bpmn_container.full_app import observe_http
from bpmn_container.transport import WORKER_URL, request_json


class ParallelTick:
    def __init__(self, *, base, token):
        self._base, self._token = base, token
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._pending = None
        self.pending_sibling_observed = False

    def tick(self):
        observed = None
        if self._pending is not None and self._pending.done():
            observed = self._pending.result()
            self._pending = None
        if self._pending is None:
            self._pending = self._pool.submit(observe_http, self._base, '/tasks/autopilot/tick',
                payload={}, headers={'Authorization': 'Bearer ' + self._token}, timeout=20)
        return observed

    def observe_join(self, *, result, tasks):
        completed = set(result.completed_node_ids)
        if not self.pending_sibling_observed and 'fast' in completed and 'slow' not in completed:
            assert 'join' not in completed, 'full_app_and_join_before_sibling'
            nodes = {task.worker_execution_context['native_node_command']['node']['node_id']: task for task in tasks}
            assert 'after' not in nodes and 'slow' in nodes, 'full_app_and_successor_before_join'
            request_json(WORKER_URL + '/release', token=os.environ['BPMN_DISPATCH_TOKEN'],
                         payload={'task_id': nodes['slow'].id})
            self.pending_sibling_observed = True

    def close(self):
        try:
            return self._pending.result() if self._pending is not None else None
        finally:
            self._pool.shutdown(wait=True, cancel_futures=True)
