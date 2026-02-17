#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Idempotenter Starter fuer den Ananta Hub in Termux.
# - prueft/installiert benoetigte Systempakete
# - legt venv an (falls nicht vorhanden)
# - installiert Python-Abhaengigkeiten (falls noetig)
# - startet den Hub-Agenten

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv-termux"
REQ_FILE="${REPO_ROOT}/requirements.txt"
STAMP_FILE="${VENV_DIR}/.requirements.sha256"

export TERMUX_APP__PACKAGE_NAME="${TERMUX_APP__PACKAGE_NAME:-com.termux}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_pkg_if_missing() {
  local pkg="$1"
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "[ok] Paket vorhanden: $pkg"
  else
    echo "[info] Installiere Paket: $pkg"
    pkg install -y "$pkg"
  fi
}

ensure_termux_packages() {
  if ! need_cmd pkg; then
    echo "[error] 'pkg' nicht gefunden. Dieses Skript ist fuer Termux gedacht."
    exit 1
  fi

  echo "[info] Paketquellen aktualisieren"
  pkg update -y >/dev/null

  install_pkg_if_missing python
  install_pkg_if_missing git
  install_pkg_if_missing libjpeg-turbo
  install_pkg_if_missing libpng
  install_pkg_if_missing libffi
  install_pkg_if_missing openssl
  install_pkg_if_missing rust
  install_pkg_if_missing clang
  install_pkg_if_missing make
}

ensure_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    echo "[info] Erstelle Virtualenv: $VENV_DIR"
    python -m venv "$VENV_DIR"
  else
    echo "[ok] Virtualenv vorhanden: $VENV_DIR"
  fi

  # shellcheck source=/dev/null
  source "${VENV_DIR}/bin/activate"
  pip install --upgrade pip setuptools wheel >/dev/null
}

requirements_hash() {
  sha256sum "$REQ_FILE" | awk '{print $1}'
}

ensure_python_requirements() {
  if [ ! -f "$REQ_FILE" ]; then
    echo "[error] requirements.txt nicht gefunden: $REQ_FILE"
    exit 1
  fi

  local current_hash
  current_hash="$(requirements_hash)"
  local installed_hash=""

  if [ -f "$STAMP_FILE" ]; then
    installed_hash="$(cat "$STAMP_FILE")"
  fi

  if [ "$current_hash" != "$installed_hash" ]; then
    echo "[info] Installiere/aktualisiere Python-Abhaengigkeiten"
    pip install -r "$REQ_FILE"
    echo "$current_hash" > "$STAMP_FILE"
  else
    echo "[ok] Python-Abhaengigkeiten sind aktuell"
  fi
}

start_hub() {
  cd "$REPO_ROOT"

  export ROLE="${ROLE:-hub}"
  export HOST="${HOST:-0.0.0.0}"
  export PORT="${PORT:-5000}"
  export DATA_DIR="${DATA_DIR:-${REPO_ROOT}/data}"
  export DATABASE_URL="${DATABASE_URL:-sqlite:///${DATA_DIR}/ananta_termux.db}"

  mkdir -p "$DATA_DIR"

  if [ ! -f "${REPO_ROOT}/.env" ]; then
    echo "[warn] Keine .env gefunden (${REPO_ROOT}/.env). Es werden Defaults genutzt."
  fi

  echo "[info] Starte Hub: ROLE=$ROLE HOST=$HOST PORT=$PORT"
  exec python -m agent.ai_agent
}

main() {
  ensure_termux_packages
  ensure_venv
  ensure_python_requirements
  start_hub
}

main "$@"
