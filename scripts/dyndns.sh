#!/usr/bin/env bash
set -euo pipefail

# DynDNS update script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${DYNDNS_CONFIG:-$SCRIPT_DIR/dyndns.conf}"

if [[ -f "$CONFIG" ]]; then
  # shellcheck source=/dev/null
  source "$CONFIG"
fi

# Supported auth modes:
# 1) DYNDNS_URL fully provided via env/config (no defaults in repository)
# 2) DYNDNS_BASE_URL + DYNDNS_LOGIN + DYNDNS_PASSWORD
if [[ -n "${DYNDNS_URL:-}" ]]; then
  URL="$DYNDNS_URL"
elif [[ -n "${DYNDNS_BASE_URL:-}" && -n "${DYNDNS_LOGIN:-}" && -n "${DYNDNS_PASSWORD:-}" ]]; then
  URL="${DYNDNS_BASE_URL}?login=${DYNDNS_LOGIN}&password=${DYNDNS_PASSWORD}"
else
  echo "ERROR: missing DynDNS credentials." >&2
  echo "Set DYNDNS_URL or DYNDNS_BASE_URL + DYNDNS_LOGIN + DYNDNS_PASSWORD." >&2
  exit 1
fi

curl -fsS --connect-timeout 10 --max-time 15 "$URL"
