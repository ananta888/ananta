#!/usr/bin/env bash
# Run the backend tests in phases, in the compose test runner (narrowest and fastest first):
#
#   scripts/test-phase.sh affected [selector args]   # tests that import or name the changed files (seconds)
#   scripts/test-phase.sh domains  [selector args]   # the whole domains (tests/<domain>) of those tests
#   scripts/test-phase.sh core                       # the default suite without the slow tier (~5 min)
#   scripts/test-phase.sh slow                       # only the slow tier (tests/slow/, slow_tests.txt, @slow)
#   scripts/test-phase.sh all                        # core + slow
#   scripts/test-phase.sh ladder [selector args]     # affected -> domains -> core, stopping at the first failure
#
# Selector args (affected/domains/ladder): --base origin/main, --files a b, --depth N (see
# scripts/select_affected_tests.py). The affected and domain phases run the core tier only (--strict-tier).
# PYTEST_WORKERS (default 8) sets the xdist workers; further pytest options go after `--`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
phase="${1:-}"
shift || true

selector_args=()
pytest_extra=()
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then
    shift
    pytest_extra=("$@")
    break
  fi
  selector_args+=("$1")
  shift
done

run_pytest() {
  local status=0
  (
    cd "$root/docker/compose-next"
    docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm --user "$(id -u):$(id -g)" t-infra \
      python -m pytest -q -p no:cacheprovider -o addopts='' -n "${PYTEST_WORKERS:-8}" --timeout=300 \
      "$@" "${pytest_extra[@]}"
  ) || status=$?
  # 5 = everything selected was deselected (e.g. only slow tests were affected): nothing failed
  if [ "$status" -eq 5 ]; then
    return 0
  fi
  return "$status"
}

selected_paths() {
  python3 "$root/scripts/select_affected_tests.py" "$@" "${selector_args[@]}"
}

run_selection() {
  local label="$1"
  shift
  local paths
  mapfile -t paths < <(selected_paths "$@")
  if [ "${#paths[@]}" -eq 0 ]; then
    echo "[$label] no affected tests" >&2
    return 0
  fi
  echo "[$label] ${#paths[@]} path(s)" >&2
  run_pytest --tier core --strict-tier "${paths[@]}"
}

case "$phase" in
  affected) run_selection affected ;;
  domains) run_selection domains --domains ;;
  core) run_pytest --tier core tests ;;
  slow) run_pytest --tier slow tests ;;
  all) run_pytest --tier all tests ;;
  ladder)
    run_selection affected
    run_selection domains --domains
    echo "[core] full core tier" >&2
    run_pytest --tier core tests
    ;;
  *)
    sed -n '2,15p' "$0" >&2
    exit 2
    ;;
esac
