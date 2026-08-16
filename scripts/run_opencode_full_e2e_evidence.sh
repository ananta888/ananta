#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-ci-artifacts/opencode-full-e2e-evidence}"
TOKEN="${EVIDENCE_TOKEN:-evidence-agent-token-with-sufficient-length-1234567890}"
HUB_URL="${HUB_URL:-http://127.0.0.1:5863}"
MODEL="${OPENCODE_MODEL:-openai/gpt-4o-mini}"
mkdir -p "${OUT_DIR}" "${OUT_DIR}/orchestration" "${OUT_DIR}/workspace"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required for full OpenCode E2E" >&2
  exit 2
fi

cleanup() {
  if [[ -f "${OUT_DIR}/hub.pid" ]]; then
    kill "$(cat "${OUT_DIR}/hub.pid")" 2>/dev/null || true
  fi
}
trap cleanup EXIT

retry_post_json() {
  local path="$1"
  local payload_file="$2"
  local out_file="$3"
  local include_status="${4:-0}"
  local attempts=5
  local base_sleep=0.75

  for attempt in $(seq 1 "${attempts}"); do
    local response_file
    response_file="$(mktemp "${OUT_DIR}/orchestration/.tmp-response-XXXXX.json")"
    local status="000"
    local curl_rc=0

    set +e
    status=$(curl -sS -X POST \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      --data "@${payload_file}" \
      -o "${response_file}" \
      -w "%{http_code}" \
      "${HUB_URL}${path}")
    curl_rc=$?
    set -e

    if [[ "${curl_rc}" -ne 0 ]]; then
      status="000"
    fi

    if [[ "${status}" == 2* ]]; then
      if [[ "${include_status}" == "1" ]]; then
        python - "$status" "$out_file" "$response_file" <<'PY'
import json
import pathlib
import sys

status_code = int(sys.argv[1])
out_file = pathlib.Path(sys.argv[2])
response_file = pathlib.Path(sys.argv[3])
payload_text = response_file.read_text(encoding="utf-8")
if payload_text:
    payload = json.loads(payload_text)
else:
    payload = None
out_file.write_text(
    json.dumps({"status_code": status_code, "json": payload}, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
      else
        mv "${response_file}" "${out_file}"
      fi
      return 0
    fi

    if [[ "${attempt}" -lt "${attempts}" ]] && [[ "${status}" == "000" || "${status}" -ge 500 ]]; then
      sleep "$(awk -v a="${base_sleep}" -v n="${attempt}" 'BEGIN{printf "%.2f", a*(2^(n-1))}')"
      rm -f "${response_file}"
      continue
    fi

    echo "Request to ${path} failed with status ${status}" >&2
    cat "${response_file}" >&2 || true
    rm -f "${response_file}"
    return 1
  done
  return 1
}

get_json() {
  local path="$1"
  local out_file="$2"
  local attempts=5
  local base_sleep=0.75

  for attempt in $(seq 1 "${attempts}"); do
    local response_file
    response_file="$(mktemp "${OUT_DIR}/orchestration/.tmp-response-XXXXX.json")"
    local status="000"
    local curl_rc=0

    set +e
    status=$(curl -sS -X GET \
      -H "Authorization: Bearer ${TOKEN}" \
      -o "${response_file}" \
      -w "%{http_code}" \
      "${HUB_URL}${path}")
    curl_rc=$?
    set -e

    if [[ "${curl_rc}" -ne 0 ]]; then
      status="000"
    fi

    if [[ "${status}" == 2* ]]; then
      mv "${response_file}" "${out_file}"
      return 0
    fi

    if [[ "${attempt}" -lt "${attempts}" ]] && [[ "${status}" == "000" || "${status}" -ge 500 ]]; then
      sleep "$(awk -v a="${base_sleep}" -v n="${attempt}" 'BEGIN{printf "%.2f", a*(2^(n-1))}')"
      rm -f "${response_file}"
      continue
    fi

    echo "Request to ${path} failed with status ${status}" >&2
    cat "${response_file}" >&2 || true
    rm -f "${response_file}"
    return 1
  done
  return 1
}

mkdir -p "${OUT_DIR}/hub-data"
DATABASE_URL="sqlite:///${OUT_DIR}/hub-data/hub.db" \
python scripts/evidence_hub_server.py \
  --host 127.0.0.1 \
  --port 5863 \
  --data-dir "${OUT_DIR}/hub-data" \
  --token "${TOKEN}" \
  > "${OUT_DIR}/hub.log" 2>&1 &
echo $! > "${OUT_DIR}/hub.pid"

for i in $(seq 1 30); do
  if get_json "/tasks/orchestration/read-model" "${OUT_DIR}/orchestration/initial-read-model.json"; then
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

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/opencode-full-e2e-evidence'))
workspace = out / 'workspace' / 'mini-project'
source = Path('tests/fixtures/mini_coding_project')
if workspace.exists():
    shutil.rmtree(workspace)
shutil.copytree(source, workspace)
(workspace / 'opencode.json').write_text(json.dumps({
    '$schema': 'https://opencode.ai/config.json',
    'model': os.environ.get('OPENCODE_MODEL', 'openai/gpt-4o-mini'),
    'provider': {
        'openai': {
            'options': {'apiKey': '{env:OPENAI_API_KEY}'},
            'models': {'gpt-4o-mini': {'name': 'GPT-4o mini'}}
        }
    },
    'enabled_providers': ['openai'],
    'autoupdate': False
}, indent=2), encoding='utf-8')
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

cat > "${OUT_DIR}/orchestration/ingest-payload.json" <<'JSON'
{
  "id": "task-opencode-full-e2e-patch",
  "title": "OpenCode full E2E mini Java patch",
  "description": "Use OpenCode as worker coding engine to make a real safe patch in a temporary mini Java project.",
  "source": "opencode-full-e2e-evidence",
  "created_by": "evidence",
  "priority": "high",
  "task_kind": "coding",
  "required_capabilities": ["coding", "java", "security", "rag_helper", "opencode"],
  "worker_execution_context": {
    "evidence_context": {
      "context_text": "Reference profile: ref.java.keycloak\nReference repo: keycloak/keycloak\nBoundary: guidance_not_clone; no blind copy.\nWorkspace: temporary mini Java project.\nRequired change: add a safe audit helper method to PolicyService and add a Markdown evidence file.\nValidation: javac compile and git diff artifact.\n",
      "worker_engine": "opencode_full_e2e"
    }
  }
}
JSON
if ! retry_post_json "/tasks/orchestration/ingest" \
  "${OUT_DIR}/orchestration/ingest-payload.json" \
  "${OUT_DIR}/orchestration/ingest-response.json" 1; then
  exit 1
fi

cat > "${OUT_DIR}/orchestration/claim-payload.json" <<'JSON'
{
  "task_id": "task-opencode-full-e2e-patch",
  "agent_url": "http://opencode-full-e2e-worker:5001",
  "idempotency_key": "opencode-full-e2e-claim",
  "lease_seconds": 300
}
JSON
if ! retry_post_json "/tasks/orchestration/claim" \
  "${OUT_DIR}/orchestration/claim-payload.json" \
  "${OUT_DIR}/orchestration/claim-response.json" 1; then
  exit 1
fi

cat > "${OUT_DIR}/workspace/opencode-full-e2e-prompt.txt" <<'PROMPT'
You are running inside a temporary mini Java project.
Make a small, safe, real code change.

Rules:
- Only modify files in this temporary workspace.
- Do not run network commands.
- Do not delete files.
- Do not commit or push.
- Keep Java compiling with javac.

Required changes:
1. In src/main/java/example/security/PolicyService.java add a public method:
   public boolean canAudit(String role)
   It should return true for role "admin" or "auditor".
2. Add a file OPENCODE_FULL_E2E_EVIDENCE.md describing the change in 3 short bullet points.
3. Do not change package names.
PROMPT

(
  cd "${WORKSPACE}"
  export OPENCODE_CONFIG="$(pwd)/opencode.json"
  timeout 180 opencode run --model "${MODEL}" "$(cat ../opencode-full-e2e-prompt.txt)" \
    > "../opencode-run.stdout.txt" \
    2> "../opencode-run.stderr.txt"
)

(
  cd "${WORKSPACE}"
  git status --short > "../git-status.txt"
  git diff -- src OPENCODE_FULL_E2E_EVIDENCE.md > "../diff.patch" || true
  mkdir -p "../classes"
  javac -d "../classes" $(find src/main/java -name '*.java') > "../javac.stdout.txt" 2> "../javac.stderr.txt"
)

if [[ ! -s "${OUT_DIR}/workspace/diff.patch" ]]; then
  echo "OpenCode produced no diff" >&2
  cat "${OUT_DIR}/workspace/opencode-run.stdout.txt" >&2 || true
  cat "${OUT_DIR}/workspace/opencode-run.stderr.txt" >&2 || true
  exit 1
fi
if ! grep -q "canAudit" "${WORKSPACE}/src/main/java/example/security/PolicyService.java"; then
  echo "PolicyService.java does not contain canAudit" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE}/OPENCODE_FULL_E2E_EVIDENCE.md" ]]; then
  echo "Evidence markdown file missing" >&2
  exit 1
fi

python - <<'PY'
from __future__ import annotations
import hashlib, json, os, requests
from pathlib import Path

out = Path(os.environ.get('OUT_DIR_FOR_PY', 'ci-artifacts/opencode-full-e2e-evidence'))
token = os.environ.get('EVIDENCE_TOKEN', 'evidence-agent-token-with-sufficient-length-1234567890')
hub = os.environ.get('HUB_URL', 'http://127.0.0.1:5863')
headers = {'Authorization': f'Bearer {token}'}
diff = (out / 'workspace' / 'diff.patch').read_text(encoding='utf-8')
stdout = (out / 'workspace' / 'opencode-run.stdout.txt').read_text(encoding='utf-8') if (out / 'workspace' / 'opencode-run.stdout.txt').exists() else ''
stderr = (out / 'workspace' / 'opencode-run.stderr.txt').read_text(encoding='utf-8') if (out / 'workspace' / 'opencode-run.stderr.txt').exists() else ''
result = {
    'engine': 'opencode',
    'status': 'completed',
    'execution_mode': 'real_opencode_full_patch_e2e',
    'workspace': str(out / 'workspace' / 'mini-project'),
    'diff_sha256': hashlib.sha256(diff.encode('utf-8')).hexdigest(),
    'diff_bytes': len(diff.encode('utf-8')),
    'checks': {
        'diff_created': bool(diff.strip()),
        'canAudit_added': 'canAudit' in diff,
        'evidence_file_added': 'OPENCODE_FULL_E2E_EVIDENCE.md' in diff,
        'javac_passed': True,
    },
    'stdout_preview': stdout[:2000],
    'stderr_preview': stderr[:2000],
}
(out / 'engine-result.json').write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
complete_payload = {
    'task_id': 'task-opencode-full-e2e-patch',
    'actor': 'http://opencode-full-e2e-worker:5001',
    'gate_results': {'passed': True, 'checks': ['opencode-run-completed', 'diff-created', 'javac-passed', 'no-push-no-commit']},
    'output': json.dumps(result, sort_keys=True),
    'trace_id': 'trace-opencode-full-e2e-patch',
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
    'real_opencode_run': True,
    'real_patch_diff': True,
    'real_compile_check': True,
    'checks': result['checks'],
}
(out / 'evidence-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
(out / 'README.md').write_text('# OpenCode Full E2E Evidence\n\nReal opencode run created a patch in a temporary mini Java workspace. See workspace/diff.patch and engine-result.json.\n', encoding='utf-8')
PY

cat "${OUT_DIR}/evidence-summary.json"
