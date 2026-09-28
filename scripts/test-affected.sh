#!/usr/bin/env bash
# Run only the tests affected by the current changes, in the compose test runner.
#   scripts/test-affected.sh                      # working tree vs. HEAD
#   scripts/test-affected.sh --base origin/main   # everything on this branch
#   scripts/test-affected.sh --depth 2            # narrower, faster feedback
# Selector arguments are passed on; PYTEST_WORKERS (default 8) sets the xdist workers.
# Tests marked `slow` (>= 10 s each) are skipped unless INCLUDE_SLOW=1; the full suite always runs them.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mapfile -t tests < <(python3 "$root/scripts/select_affected_tests.py" "$@")
if [ "${#tests[@]}" -eq 0 ]; then
  echo "no affected tests"
  exit 0
fi
marker=(-m "not slow")
if [ "${INCLUDE_SLOW:-0}" = "1" ]; then
  marker=()
fi
echo "running ${#tests[@]} test path(s)${marker:+ (without slow tests; INCLUDE_SLOW=1 adds them)}" >&2
cd "$root/docker/compose-next"
status=0
docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm --user "$(id -u):$(id -g)" t-infra \
  python -m pytest -q -p no:cacheprovider -o addopts='' -n "${PYTEST_WORKERS:-8}" --timeout=300 "${marker[@]}" "${tests[@]}" \
  || status=$?
# 5 = every selected test was deselected (e.g. only slow tests were affected): nothing failed
if [ "$status" -eq 5 ]; then
  exit 0
fi
exit "$status"
