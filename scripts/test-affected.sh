#!/usr/bin/env bash
# Run only the tests affected by the current changes, in the compose test runner.
#   scripts/test-affected.sh                      # working tree vs. HEAD
#   scripts/test-affected.sh --base origin/main   # everything on this branch
#   scripts/test-affected.sh --depth 2            # narrower, faster feedback
# Selector arguments are passed on; PYTEST_WORKERS (default 8) sets the xdist workers.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mapfile -t tests < <(python3 "$root/scripts/select_affected_tests.py" "$@")
if [ "${#tests[@]}" -eq 0 ]; then
  echo "no affected tests"
  exit 0
fi
echo "running ${#tests[@]} test path(s)" >&2
cd "$root/docker/compose-next"
exec docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm --user "$(id -u):$(id -g)" t-infra \
  python -m pytest -q -p no:cacheprovider -o addopts='' -n "${PYTEST_WORKERS:-8}" --timeout=300 "${tests[@]}"
