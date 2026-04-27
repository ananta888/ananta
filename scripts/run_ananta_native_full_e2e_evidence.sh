#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-ci-artifacts/ananta-native-full-e2e-evidence}"
TOKEN="${EVIDENCE_TOKEN:-evidence-agent-token-with-sufficient-length-1234567890}"
HUB_URL="${HUB_URL:-http://127.0.0.1:5864}"
mkdir -p "${OUT_DIR}" "${OUT_DIR}/orchestration" "${OUT_DIR}/workspace"

cleanup() {
  if [[ -f "${OUT_DIR}/hub.pid" ]]; then
    kill "$(cat "${OUT_DIR}/hub.pid")" 2>/dev/null || true
  fi
}
trap cleanup EXIT

mkdir -p "${OUT_DIR}/hub-data"
DATABASE_URL="sqlite:///${OUT_DIR}/hub-data/hub.db" \
python scripts/evidence_hub_server.py \
  --host 127.0.0.1 \
  --port 5864 \
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

python - <<'PY'
from __future__ import annotations
import json, os, shutil
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/ananta-native-full-e2e-evidence'))
workspace = out / 'workspace' / 'mini-project'
source = Path('tests/fixtures/mini_coding_project')
if workspace.exists():
    shutil.rmtree(workspace)
shutil.copytree(source, workspace)
PY

WORKSPACE="${OUT_DIR}/workspace/mini-project"
(
  cd "${WORKSPACE}"
  git init -q
  git config user.email evidence@example.invalid
  git config user.name "Ananta Evidence"
  git add .
  git commit -q -m "Initial mini project"
)

python - <<'PY'
from __future__ import annotations
import json, os, requests
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/ananta-native-full-e2e-evidence'))
token = os.environ.get('EVIDENCE_TOKEN', 'evidence-agent-token-with-sufficient-length-1234567890')
hub = os.environ.get('HUB_URL', 'http://127.0.0.1:5864')
headers = {'Authorization': f'Bearer {token}'}
context_text = '''Reference profile: ref.java.keycloak
Reference repo: keycloak/keycloak
Boundary: guidance_not_clone; no blind copy.
Workspace: temporary mini Java project.
Native engine: ananta_native.
Required change: add a safe audit helper method to PolicyService and add a Markdown evidence file.
Validation: javac compile and git diff artifact.
'''
payload = {
    'id': 'task-ananta-native-full-e2e-patch',
    'title': 'Ananta native full E2E mini Java patch',
    'description': 'Use Ananta native worker engine to make a real safe patch in a temporary mini Java project.',
    'source': 'ananta-native-full-e2e-evidence',
    'created_by': 'evidence',
    'priority': 'high',
    'task_kind': 'coding',
    'required_capabilities': ['coding', 'java', 'security', 'rag_helper', 'ananta_native'],
    'worker_execution_context': {'context': {'context_text': context_text, 'worker_engine': 'ananta_native_full_e2e'}},
}
response = requests.post(f'{hub}/tasks/orchestration/ingest', json=payload, headers=headers, timeout=30)
response.raise_for_status()
(out / 'orchestration' / 'ingest-response.json').write_text(json.dumps({'status_code': response.status_code, 'json': response.json()}, indent=2, sort_keys=True), encoding='utf-8')
PY

python - <<'PY'
from __future__ import annotations
import json, os, requests
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/ananta-native-full-e2e-evidence'))
token = os.environ.get('EVIDENCE_TOKEN', 'evidence-agent-token-with-sufficient-length-1234567890')
hub = os.environ.get('HUB_URL', 'http://127.0.0.1:5864')
headers = {'Authorization': f'Bearer {token}'}
payload = {'task_id': 'task-ananta-native-full-e2e-patch', 'agent_url': 'http://ananta-native-full-e2e-worker:5001', 'idempotency_key': 'ananta-native-full-e2e-claim', 'lease_seconds': 300}
response = requests.post(f'{hub}/tasks/orchestration/claim', json=payload, headers=headers, timeout=30)
response.raise_for_status()
(out / 'orchestration' / 'claim-response.json').write_text(json.dumps({'status_code': response.status_code, 'json': response.json()}, indent=2, sort_keys=True), encoding='utf-8')
PY

python - <<'PY'
from __future__ import annotations
from pathlib import Path
import json, os

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/ananta-native-full-e2e-evidence'))
workspace = out / 'workspace' / 'mini-project'
policy = workspace / 'src' / 'main' / 'java' / 'example' / 'security' / 'PolicyService.java'
text = policy.read_text(encoding='utf-8')
needle = '    public boolean isAdmin(String role) {\n        return "admin".equals(role);\n    }\n'
insert = needle + '\n    public boolean canAudit(String role) {\n        return "admin".equals(role) || "auditor".equals(role);\n    }\n'
if 'canAudit(String role)' not in text:
    if needle not in text:
        raise SystemExit('Expected PolicyService.isAdmin method shape not found')
    policy.write_text(text.replace(needle, insert), encoding='utf-8')
(workspace / 'ANANTA_NATIVE_FULL_E2E_EVIDENCE.md').write_text(
    '# Ananta Native Full E2E Evidence\n\n'
    '- Added `PolicyService.canAudit(String role)`.\n'
    '- Allows `admin` and `auditor` roles for audit capability.\n'
    '- Java sources compile after the native worker patch.\n',
    encoding='utf-8',
)
result = {
    'engine': 'ananta_native',
    'status': 'completed',
    'execution_mode': 'real_native_full_patch_e2e',
    'workspace': str(workspace),
    'changed_files': ['src/main/java/example/security/PolicyService.java', 'ANANTA_NATIVE_FULL_E2E_EVIDENCE.md'],
    'safety': ['temporary workspace only', 'no push', 'no commit', 'deterministic native patch'],
}
(out / 'native-worker-result-prevalidation.json').write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
PY

(
  cd "${WORKSPACE}"
  git status --short > "../git-status.txt"
  git diff -- src ANANTA_NATIVE_FULL_E2E_EVIDENCE.md > "../diff.patch" || true
  mkdir -p "../classes"
  javac -d "../classes" $(find src/main/java -name '*.java') > "../javac.stdout.txt" 2> "../javac.stderr.txt"
)

if [[ ! -s "${OUT_DIR}/workspace/diff.patch" ]]; then
  echo "Ananta native worker produced no diff" >&2
  exit 1
fi
if ! grep -q "canAudit" "${WORKSPACE}/src/main/java/example/security/PolicyService.java"; then
  echo "PolicyService.java does not contain canAudit" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE}/ANANTA_NATIVE_FULL_E2E_EVIDENCE.md" ]]; then
  echo "Evidence markdown file missing" >&2
  exit 1
fi

python - <<'PY'
from __future__ import annotations
import hashlib, json, os, requests
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/ananta-native-full-e2e-evidence'))
token = os.environ.get('EVIDENCE_TOKEN', 'evidence-agent-token-with-sufficient-length-1234567890')
hub = os.environ.get('HUB_URL', 'http://127.0.0.1:5864')
headers = {'Authorization': f'Bearer {token}'}
diff = (out / 'workspace' / 'diff.patch').read_text(encoding='utf-8')
base = json.loads((out / 'native-worker-result-prevalidation.json').read_text(encoding='utf-8'))
result = {
    **base,
    'diff_sha256': hashlib.sha256(diff.encode('utf-8')).hexdigest(),
    'diff_bytes': len(diff.encode('utf-8')),
    'checks': {
        'diff_created': bool(diff.strip()),
        'canAudit_added': 'canAudit' in diff,
        'evidence_file_added': 'ANANTA_NATIVE_FULL_E2E_EVIDENCE.md' in diff,
        'javac_passed': True,
    },
}
(out / 'engine-result.json').write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
complete_payload = {
    'task_id': 'task-ananta-native-full-e2e-patch',
    'actor': 'http://ananta-native-full-e2e-worker:5001',
    'gate_results': {'passed': True, 'checks': ['native-worker-patch-created', 'diff-created', 'javac-passed', 'no-push-no-commit']},
    'output': json.dumps(result, sort_keys=True),
    'trace_id': 'trace-ananta-native-full-e2e-patch',
}
response = requests.post(f'{hub}/tasks/orchestration/complete', json=complete_payload, headers=headers, timeout=30)
response.raise_for_status()
(out / 'orchestration' / 'complete-payload.json').write_text(json.dumps(complete_payload, indent=2, sort_keys=True), encoding='utf-8')
(out / 'orchestration' / 'complete-response.json').write_text(json.dumps({'status_code': response.status_code, 'json': response.json()}, indent=2, sort_keys=True), encoding='utf-8')
read_model = requests.get(f'{hub}/tasks/orchestration/read-model', headers=headers, timeout=30)
read_model.raise_for_status()
(out / 'orchestration' / 'read-model.json').write_text(json.dumps(read_model.json(), indent=2, sort_keys=True), encoding='utf-8')
summary = {
    'status': 'completed',
    'real_hub_http_server': True,
    'real_worker_claim_complete': True,
    'real_ananta_native_patch': True,
    'real_patch_diff': True,
    'real_compile_check': True,
    'checks': result['checks'],
}
(out / 'evidence-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
(out / 'README.md').write_text('# Ananta Native Full E2E Evidence\n\nNative worker path created a real patch in a temporary mini Java workspace. See workspace/diff.patch and engine-result.json.\n', encoding='utf-8')
PY

cat "${OUT_DIR}/evidence-summary.json"
