"""Deterministic Hub-owned TODO completion metadata, after verified execution."""
from __future__ import annotations

import hashlib
import json

from agent.services.self_development.contracts import flatten_tasks, source_path
from agent.services.self_development.workspace import read_source


def seal_completion(policy, item):
    source = item['source']
    if source == 'user-submission':
        return
    source_path(source)
    if not source.startswith('todos/') and not source.startswith('todo.'):
        raise ValueError('todo_completion_source_denied')
    document = json.loads(read_source(policy.checkout, source))
    matches = [t for t in flatten_tasks(document) if str(t['id']) == item['todo_id']]
    if len(matches) != 1 or matches[0].get('execution') != item.get('source_execution'):
        raise ValueError('todo_completion_contract_changed')
    task = matches[0]
    task['status'] = 'done'
    task['execution_result'] = {'scope': 'local', 'run_id': item['outcome']['run_id'],
        'source_ids': item['outcome']['source_ids'], 'result_digest': item['outcome']['result_digest'],
        'completion_rule': 'profile_passed'}
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()
    sealed = policy.root / 'sealed' / item['task_id'] / source
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_bytes(encoded)
    sealed.chmod(0o400)
    item['hashes'][source] = hashlib.sha256(encoded).hexdigest()
