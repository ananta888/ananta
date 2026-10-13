"""Compare immutable scope observations, not claims about private reasoning."""

import hashlib
import json


def memory_observation_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def compare_memory_snapshots(before: dict, after: dict | None) -> dict:
    if before['phase'] != 'before':
        raise ValueError('memory_snapshot_phase_invalid')
    base = {key: before[key] for key in ('run_id', 'channel_id', 'scope', 'namespace', 'policy_digest')}
    result = {**base, 'attribution': 'observed_scope_change', 'before_snapshot_id': before['snapshot_id'],
              'after_snapshot_id': None, 'audit_event_ids': [before['audit_event_id']],
              'status': 'incomplete', 'changes': [], 'production_release_evidence': False,
              'deletion_semantics': 'absent_from_observed_scope'}
    if after is None:
        return result
    if after['phase'] != 'after' or any(after[key] != value for key, value in base.items()):
        raise ValueError('memory_snapshot_pair_binding_invalid')
    old = {item['entry_id']: item for item in before['entries']}
    new = {item['entry_id']: item for item in after['entries']}
    if len(old) != len(before['entries']) or len(new) != len(after['entries']):
        raise ValueError('memory_snapshot_entry_duplicate')
    changes = []
    for entry_id in sorted(old.keys() | new.keys()):
        prior, current = old.get(entry_id), new.get(entry_id)
        if prior == current:
            continue
        kind = 'added' if prior is None else 'deleted' if current is None else 'modified'
        changes.append({'entry_id': entry_id, 'kind': kind, 'before': prior, 'after': current})
    return {**result, 'status': 'complete', 'changes': changes, 'scope_outcome': after['scope_outcome'],
            'after_snapshot_id': after['snapshot_id'],
            'audit_event_ids': [before['audit_event_id'], after['audit_event_id']]}
