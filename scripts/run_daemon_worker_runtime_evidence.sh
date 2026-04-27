#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-ci-artifacts/daemon-worker-runtime-evidence}"
TOKEN="${EVIDENCE_TOKEN:-evidence-agent-token-with-sufficient-length-1234567890}"
HUB_URL="${HUB_URL:-http://127.0.0.1:5862}"
mkdir -p "${OUT_DIR}" "${OUT_DIR}/orchestration" "${OUT_DIR}/daemon-workers" "${OUT_DIR}/daemon-workers/ananta_native" "${OUT_DIR}/daemon-workers/opencode"

cleanup() {
  if [[ -f "${OUT_DIR}/daemon-workers/ananta_native.pid" ]]; then
    kill "$(cat "${OUT_DIR}/daemon-workers/ananta_native.pid")" 2>/dev/null || true
  fi
  if [[ -f "${OUT_DIR}/daemon-workers/opencode.pid" ]]; then
    kill "$(cat "${OUT_DIR}/daemon-workers/opencode.pid")" 2>/dev/null || true
  fi
  if [[ -f "${OUT_DIR}/hub.pid" ]]; then
    kill "$(cat "${OUT_DIR}/hub.pid")" 2>/dev/null || true
  fi
}
trap cleanup EXIT

python - <<'PY'
from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

repo = Path.cwd()
out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/daemon-worker-runtime-evidence'))
project = repo / 'tests' / 'fixtures' / 'mini_coding_project'
files = ['SecurityController.java', 'TokenVerifier.java', 'PolicyService.java']

os.environ.setdefault('DATABASE_URL', f"sqlite:///{out / 'preindex.db'}")
os.environ.setdefault('CONTROLLER_URL', 'http://mock-controller')
os.environ.setdefault('AGENT_NAME', 'daemon-evidence-preindex')
os.environ.setdefault('INITIAL_ADMIN_USER', 'admin')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'admin')

from agent.database import init_db
from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService

init_db()
records = []
for filename in files:
    source = project / 'src' / 'main' / 'java' / 'example' / 'security' / filename
    artifact, _version, _collection = IngestionService().upload_artifact(
        filename=filename,
        content=source.read_bytes(),
        created_by='evidence',
        media_type='text/x-java-source',
    )
    _index, run = RagHelperIndexService().index_artifact(
        artifact.id,
        created_by='evidence',
        profile_name='deep_code',
    )
    output_dir = Path(str(run.output_dir))
    target = out / 'preindex-rag-helper' / filename
    target.mkdir(parents=True, exist_ok=True)
    for name in ['manifest.json', 'index.jsonl']:
        src = output_dir / name
        if src.exists():
            shutil.copy2(src, target / name)
    index_file = output_dir / 'index.jsonl'
    if index_file.exists():
        for line in index_file.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
context = {
    'index_kind': 'real_rag_helper_index_service',
    'profile_name': 'deep_code',
    'record_count': len(records),
    'records_preview': records[:12],
}
(out / 'preindex-rag-helper').mkdir(parents=True, exist_ok=True)
(out / 'preindex-rag-helper' / 'rag-context.json').write_text(json.dumps(context, indent=2, sort_keys=True), encoding='utf-8')
PY

mkdir -p "${OUT_DIR}/hub-data"
DATABASE_URL="sqlite:///${OUT_DIR}/hub-data/hub.db" \
python scripts/evidence_hub_server.py \
  --host 127.0.0.1 \
  --port 5862 \
  --data-dir "${OUT_DIR}/hub-data" \
  --token "${TOKEN}" \
  > "${OUT_DIR}/hub.log" 2>&1 &
echo $! > "${OUT_DIR}/hub.pid"

for i in $(seq 1 30); do
  if curl -fsS -H "Authorization: Bearer ${TOKEN}" "${HUB_URL}/tasks/orchestration/read-model" > "${OUT_DIR}/orchestration/initial-read-model.json" 2>/dev/null; then
    break
  fi
  sleep 1
  if [[ "$i" == "30" ]]; then
    echo "Hub did not become ready" >&2
    cat "${OUT_DIR}/hub.log" >&2 || true
    exit 1
  fi
done

mkdir -p "${OUT_DIR}/daemon-workers/ananta_native" "${OUT_DIR}/daemon-workers/opencode"

python scripts/evidence_worker_daemon.py \
  --hub-url "${HUB_URL}" \
  --token "${TOKEN}" \
  --engine ananta_native \
  --out "${OUT_DIR}/daemon-workers/ananta_native" \
  --agent-url http://daemon-evidence-worker-ananta-native:5001 \
  --poll-timeout 75 \
  --command-timeout 30 \
  > "${OUT_DIR}/daemon-workers/ananta_native/process.log" 2>&1 &
echo $! > "${OUT_DIR}/daemon-workers/ananta_native.pid"

python scripts/evidence_worker_daemon.py \
  --hub-url "${HUB_URL}" \
  --token "${TOKEN}" \
  --engine opencode \
  --out "${OUT_DIR}/daemon-workers/opencode" \
  --agent-url http://daemon-evidence-worker-opencode:5001 \
  --poll-timeout 75 \
  --command-timeout 30 \
  > "${OUT_DIR}/daemon-workers/opencode/process.log" 2>&1 &
echo $! > "${OUT_DIR}/daemon-workers/opencode.pid"

python - <<'PY'
from __future__ import annotations
import json, os, requests
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/daemon-worker-runtime-evidence'))
token = os.environ.get('EVIDENCE_TOKEN', 'evidence-agent-token-with-sufficient-length-1234567890')
hub = os.environ.get('HUB_URL', 'http://127.0.0.1:5862')
context = json.loads((out / 'preindex-rag-helper' / 'rag-context.json').read_text(encoding='utf-8'))
context_text = (
    'Reference profile: ref.java.keycloak\n'
    'Reference repo: keycloak/keycloak\n'
    'Boundary: guidance_not_clone; no blind copy.\n\n'
    + json.dumps(context, indent=2, sort_keys=True)
)
headers = {'Authorization': f'Bearer {token}'}
results = {}
for engine in ['ananta_native', 'opencode']:
    payload = {
        'id': f'task-daemon-worker-{engine}',
        'title': f'Daemon worker evidence for {engine}',
        'description': f'Daemon worker should poll, claim and complete using engine={engine}.',
        'source': 'daemon-worker-runtime-evidence',
        'created_by': 'evidence',
        'priority': 'high',
        'task_kind': 'coding',
        'required_capabilities': ['coding', 'java', 'security', 'rag_helper', engine],
        'worker_execution_context': {
            'context': {
                'context_text': context_text,
                'reference_profile_id': 'ref.java.keycloak',
                'rag_index_kind': 'real_rag_helper_index_service',
                'worker_engine': engine,
            }
        },
    }
    response = requests.post(f'{hub}/tasks/orchestration/ingest', json=payload, headers=headers, timeout=30)
    results[engine] = {'status_code': response.status_code, 'json': response.json() if response.text else {}}
    response.raise_for_status()
(out / 'orchestration').mkdir(parents=True, exist_ok=True)
(out / 'orchestration' / 'ingest-results.json').write_text(json.dumps(results, indent=2, sort_keys=True), encoding='utf-8')
PY

ANANTA_PID="$(cat "${OUT_DIR}/daemon-workers/ananta_native.pid")"
OPENCODE_PID="$(cat "${OUT_DIR}/daemon-workers/opencode.pid")"
wait "${ANANTA_PID}"
wait "${OPENCODE_PID}"

curl -fsS -H "Authorization: Bearer ${TOKEN}" "${HUB_URL}/tasks/orchestration/read-model" > "${OUT_DIR}/orchestration/read-model.json"

python - <<'PY'
from __future__ import annotations
import json, os
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/daemon-worker-runtime-evidence'))
read_model = json.loads((out / 'orchestration' / 'read-model.json').read_text(encoding='utf-8'))
summaries = {}
for engine in ['ananta_native', 'opencode']:
    summaries[engine] = json.loads((out / 'daemon-workers' / engine / 'daemon-summary.json').read_text(encoding='utf-8'))
summary = {
    'status': 'completed',
    'real_hub_http_server': True,
    'real_daemon_polling_workers': True,
    'real_opencode_cli': True,
    'daemon_summaries': summaries,
    'checks': {
        'ananta_native_daemon_completed': summaries['ananta_native'].get('status') == 'completed',
        'opencode_daemon_completed': summaries['opencode'].get('status') == 'completed',
        'opencode_real_cli': summaries['opencode'].get('real_opencode_cli') is True,
        'read_model_completed_tasks': read_model['data']['queue'].get('completed', 0) >= 2 if 'data' in read_model else read_model['json']['data']['queue'].get('completed', 0) >= 2,
    },
}
(out / 'evidence-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
(out / 'README.md').write_text('# Daemon Worker Runtime Evidence\n\nReal local Hub HTTP server plus two polling worker processes.\n', encoding='utf-8')
failed = [key for key, value in summary['checks'].items() if not value]
if failed:
    raise SystemExit(f'Evidence checks failed: {failed}')
PY

cat "${OUT_DIR}/evidence-summary.json"
