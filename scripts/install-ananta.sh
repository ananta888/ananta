#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${ANANTA_INSTALL_DIR:-$HOME/ananta}"
REF="main"
ALLOW_DIRTY=0

usage() {
  cat <<'EOF'
Install Ananta from a single script.

Usage:
  scripts/install-ananta.sh [options]

Options:
  --install-dir <path>   Target install directory (default: $HOME/ananta)
  --ref <branch-or-tag>  Git branch/tag/ref to checkout (default: main)
  --allow-dirty          Allow updating an existing dirty checkout
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    --allow-dirty|--force)
      ALLOW_DIRTY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
platform_hint() {
  local tool="$1"
  case "$OS_NAME" in
    Darwin)
      if [[ "$tool" == "git" ]]; then
        echo "Install with Homebrew: brew install git"
      else
        echo "Install Python from https://www.python.org/downloads/macos/ or with Homebrew: brew install python"
      fi
      ;;
    Linux)
      if [[ "$tool" == "git" ]]; then
        echo "Install on Ubuntu/Debian: sudo apt update && sudo apt install -y git"
      else
        echo "Install on Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv"
      fi
      ;;
    *)
      if [[ "$tool" == "git" ]]; then
        echo "Install Git from https://git-scm.com/downloads"
      else
        echo "Install Python from https://www.python.org/downloads/"
      fi
      ;;
  esac
}

echo "Detected platform: $OS_NAME"

if ! has_cmd git; then
  echo "Error: git is required. $(platform_hint git)" >&2
  exit 1
fi

PYTHON_CMD=""
if has_cmd python3; then
  PYTHON_CMD="python3"
elif has_cmd python; then
  PYTHON_CMD="python"
else
  echo "Error: Python is required. $(platform_hint python)" >&2
  exit 1
fi

INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"
if [[ "$INSTALL_DIR" != /* ]]; then
  INSTALL_DIR="$(pwd)/$INSTALL_DIR"
fi
mkdir -p "$(dirname "$INSTALL_DIR")"

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  if [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    echo "Error: install dir exists and is not an Ananta git checkout: $INSTALL_DIR" >&2
    exit 1
  fi
  echo "Cloning Ananta into $INSTALL_DIR ..."
  git clone --branch "$REF" --single-branch https://github.com/ananta888/ananta.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if [[ -n "$(git status --porcelain)" && "$ALLOW_DIRTY" -ne 1 ]]; then
  echo "Error: existing checkout is dirty. Commit/stash changes or rerun with --allow-dirty." >&2
  exit 1
fi

VENV_PY=""
if [[ -x ".venv/bin/python" ]]; then
  VENV_PY=".venv/bin/python"
fi

if [[ -n "$VENV_PY" ]]; then
  UPDATE_CMD=("$VENV_PY" -m agent.cli.main update --repo-dir "$INSTALL_DIR" --ref "$REF")
  if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
    UPDATE_CMD+=(--allow-dirty)
  fi
  echo "Running unified update path: ${UPDATE_CMD[*]}"
  if ! "${UPDATE_CMD[@]}"; then
    echo "Warning: unified update command failed, falling back to installer update flow."
    git fetch --tags --prune origin
    git checkout "$REF"
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    if [[ "$BRANCH" != "HEAD" ]]; then
      git pull --ff-only origin "$BRANCH"
    fi
  fi
else
  git fetch --tags --prune origin
  git checkout "$REF"
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$BRANCH" != "HEAD" ]]; then
    git pull --ff-only origin "$BRANCH"
  fi
fi

if [[ ! -d ".venv" ]]; then
  "$PYTHON_CMD" -m venv .venv
fi

VENV_PY=".venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Error: virtualenv python not found at $VENV_PY" >&2
  exit 1
fi

"$VENV_PY" -m pip install --upgrade pip
if [[ -f "requirements.lock" ]]; then
  "$VENV_PY" -m pip install -r requirements.lock
elif [[ -f "requirements.txt" ]]; then
  "$VENV_PY" -m pip install -r requirements.txt
fi
if [[ -f "requirements-dev.lock" ]]; then
  "$VENV_PY" -m pip install -r requirements-dev.lock
fi
"$VENV_PY" -m pip install -e .
"$VENV_PY" -m agent.cli.main --help >/dev/null

cat <<EOF
Ananta installation completed.
Install dir: $INSTALL_DIR

Next steps:
  $VENV_PY -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
  $VENV_PY -m agent.cli.main doctor
  $VENV_PY -m agent.cli.main status

Runtime examples:
  Local Ollama:
    $VENV_PY -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
  OpenAI-compatible:
    $VENV_PY -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend openai-compatible --endpoint-url http://localhost:1234/v1 --model your-model

Note: this installer does not store API keys; configure provider credentials in your shell/profile.
EOF
