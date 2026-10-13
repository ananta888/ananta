"""Bounded diagnostic counters survive cleanup of an owned acceptance container."""

import json
from collections import Counter

from sqlalchemy import select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from tests.meet_dialog_browser_fixture import docker


def record_companion_cleanup_snapshot(container, database, task_id, counts, record_property, *, enabled):
    if not enabled:
        return
    code = '''
import json
from pathlib import Path
result={}
for name in ('real-gpu-tts-observation','synthetic-sync-observation','synthetic-drift-observation',
             'synthetic-renewal-observation','synthetic-session-observation'):
 path=Path('/state')/(name+'.json')
 if path.is_file():
  with path.open('rb') as source: raw=source.read(65537)
  if len(raw)>65536: raise ValueError('test_observation_oversize')
  result[name]=json.loads(raw)
print(json.dumps(result))
'''
    running = docker('inspect', container.name, '--format', '{{.State.Running}}')
    if running not in {'true', 'false'}:
        raise ValueError('test_container_state_invalid')
    # Intentional crash acceptance leaves an owned, stopped container. Its
    # authoritative Hub SQL result is still observable, but exec is impossible.
    observation = (json.loads(docker('exec', container.name, 'python', '-S', '-c', code))
                   if running == 'true' else {})
    # Never export transcripts, model/tool content, secrets or evidence IDs.
    tts = [{key: value[key] for key in ('started', 'finished', 'seconds', 'samples', 'cache_bytes') if key in value}
           for value in observation.get('real-gpu-tts-observation', [])]
    with database.connect() as connection:
        states = connection.execute(select(HubRunEvidenceIdentityDB.state).where(
            HubRunEvidenceIdentityDB.task_id == task_id)).scalars().all()
    record_property('companion_cleanup_snapshot', json.dumps({'callbacks': dict(counts),
        'tts': tts, 'synchronization': observation.get('synthetic-sync-observation', {}),
        'drift': observation.get('synthetic-drift-observation', []),
        'renewal': observation.get('synthetic-renewal-observation', {}),
        'session': observation.get('synthetic-session-observation', {}),
        'hub_run_states': dict(Counter(states)), 'container_running': running == 'true',
        'browser_observation_available': running == 'true',
        'production_release_evidence': False}, sort_keys=True))
