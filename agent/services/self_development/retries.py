"""Bounded Hub retries always use a fresh Task, workspace and reserved run."""
from __future__ import annotations

import time
import uuid

from agent.services.self_development.budget import (
    attempt_budget_exhausted,
    session_active,
    task_budget_exhausted,
)


def prepare_retry(item, policy, state, queue):
    attempts = item.setdefault('attempts', [])
    if (not item.get('contract') or item.get('outcome', {}).get('passed')
            or attempt_budget_exhausted(policy.value, attempts)
            or (policy.root / 'stop').exists()
            or not session_active(policy.value, state.get('session'), time.time())
            or task_budget_exhausted(policy.value, state.get('session') or {})):
        return False
    policy.assert_current()
    attempts.append({k: item.get(k) for k in ('task_id', 'reason', 'outcome', 'revision')})
    for key in ('assignment', 'outcome', 'hashes', 'revision', 'worker_url', 'commit_sha'):
        item.pop(key, None)
    item.update(task_id=str(uuid.uuid4()), status='ready', reason='retry_pending')
    queue.ingest_task(task_id=item['task_id'], status='todo', title=item['title'],
        description=item['description'], created_by='self-development-hub', source='system',
        team_id=policy.value['team_id'], tags=['self-development', item['todo_id']],
        event_details={'retry_of': attempts[-1]['task_id'], 'attempt': len(attempts) + 1},
        extra_fields={'task_kind': 'coding', 'required_capabilities': ['coding', 'testing',
                                                                     'workflow.adapter.native']})
    return True
