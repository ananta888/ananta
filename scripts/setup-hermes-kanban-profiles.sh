#!/usr/bin/env bash
set -euo pipefail

# Setup Hermes profiles for CLI-only Kanban multi-agent work.
#
# This script intentionally does not store API keys. Export OPENROUTER_API_KEY
# in your shell or put it into the relevant Hermes profile .env files yourself.
#
# Cost strategy:
#   Default profile models are free OpenRouter models first. Paid models are only
#   configured as explicit fallback_model entries where Hermes supports fallback.
#   This keeps normal usage cheap while still allowing a manual/automatic fallback
#   path when a free model is unavailable, rate-limited, or not good enough.
#
# Defaults can be overridden through environment variables:
#
#   PROJECT_DIR=/home/krusty/ananta \
#   FREE_PRIMARY_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
#   CHEAP_FALLBACK_MODEL=minimax/minimax-m3 \
#   ./scripts/setup-hermes-kanban-profiles.sh
#
# Optional:
#   AUTO_DECOMPOSE=true ./scripts/setup-hermes-kanban-profiles.sh
#   USE_PAID_PRIMARY=true ./scripts/setup-hermes-kanban-profiles.sh

PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"
PROVIDER="${HERMES_PROVIDER:-openrouter}"
BASE_URL="${HERMES_BASE_URL:-https://openrouter.ai/api/v1}"

# Free-first defaults. These IDs are OpenRouter IDs. Free models are sometimes
# rate-limited or temporarily unavailable; adjust through env vars when needed.
FREE_PRIMARY_MODEL="${FREE_PRIMARY_MODEL:-nvidia/nemotron-3-ultra-550b-a55b:free}"
FREE_FAST_MODEL="${FREE_FAST_MODEL:-nvidia/nemotron-3.5-content-safety:free}"
FREE_SUMMARIZER_MODEL="${FREE_SUMMARIZER_MODEL:-z-ai/glm-4.5-air:free}"

# Cheapest useful paid fallback defaults. MiniMax M3 is currently cheap for long
# context agentic/coding work, but not free. Override when OpenRouter pricing or
# availability changes.
CHEAP_FALLBACK_MODEL="${CHEAP_FALLBACK_MODEL:-minimax/minimax-m3}"
CHEAP_FAST_FALLBACK_MODEL="${CHEAP_FAST_FALLBACK_MODEL:-google/gemini-2.5-flash-lite}"
CHEAP_REVIEW_FALLBACK_MODEL="${CHEAP_REVIEW_FALLBACK_MODEL:-deepseek/deepseek-v3}"

# If set to true, use the cheap paid fallback models as primary defaults. This is
# useful when free models are too rate-limited for a focused work session.
USE_PAID_PRIMARY="${USE_PAID_PRIMARY:-false}"

if [[ "$USE_PAID_PRIMARY" == "true" ]]; then
  ORCHESTRATOR_MODEL="${ORCHESTRATOR_MODEL:-$CHEAP_FALLBACK_MODEL}"
  CODER_MODEL="${CODER_MODEL:-$CHEAP_FALLBACK_MODEL}"
  REVIEWER_MODEL="${REVIEWER_MODEL:-$CHEAP_REVIEW_FALLBACK_MODEL}"
  RESEARCHER_MODEL="${RESEARCHER_MODEL:-$CHEAP_FALLBACK_MODEL}"
  SUMMARIZER_MODEL="${SUMMARIZER_MODEL:-$CHEAP_FAST_FALLBACK_MODEL}"
else
  ORCHESTRATOR_MODEL="${ORCHESTRATOR_MODEL:-$FREE_PRIMARY_MODEL}"
  CODER_MODEL="${CODER_MODEL:-$FREE_PRIMARY_MODEL}"
  REVIEWER_MODEL="${REVIEWER_MODEL:-$FREE_PRIMARY_MODEL}"
  RESEARCHER_MODEL="${RESEARCHER_MODEL:-$FREE_PRIMARY_MODEL}"
  SUMMARIZER_MODEL="${SUMMARIZER_MODEL:-$FREE_SUMMARIZER_MODEL}"
fi

KANBAN_DECOMPOSER_MODEL="${KANBAN_DECOMPOSER_MODEL:-$ORCHESTRATOR_MODEL}"
TRIAGE_SPECIFIER_MODEL="${TRIAGE_SPECIFIER_MODEL:-$FREE_FAST_MODEL}"
PROFILE_DESCRIBER_MODEL="${PROFILE_DESCRIBER_MODEL:-$FREE_FAST_MODEL}"

AUTO_DECOMPOSE="${AUTO_DECOMPOSE:-false}"
DEFAULT_ASSIGNEE="${DEFAULT_ASSIGNEE:-reviewer}"
ORCHESTRATOR_PROFILE="${ORCHESTRATOR_PROFILE:-orchestrator}"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 1
  fi
}

hermes_set() {
  local profile="$1"
  local key="$2"
  local value="$3"
  echo "[$profile] config set $key=$value"
  hermes -p "$profile" config set "$key" "$value"
}

hermes_set_optional() {
  local profile="$1"
  local key="$2"
  local value="$3"
  echo "[$profile] optional config set $key=$value"
  hermes -p "$profile" config set "$key" "$value" >/dev/null 2>&1 || true
}

create_profile() {
  local profile="$1"
  local description="$2"

  echo "Ensuring Hermes profile exists: $profile"
  if hermes profile create "$profile" --description "$description" >/dev/null 2>&1; then
    echo "  created: $profile"
  else
    echo "  already exists or create returned non-zero; continuing: $profile"
    # Keep description current when the profile already exists. If this command is
    # not supported by the installed Hermes version, do not fail the whole setup.
    hermes profile describe "$profile" --text "$description" >/dev/null 2>&1 || true
  fi
}

configure_openrouter_profile() {
  local profile="$1"
  local model="$2"
  local fallback_model="$3"

  hermes_set "$profile" "model.provider" "$PROVIDER"
  hermes_set "$profile" "model.default" "$model"
  hermes_set "$profile" "model.base_url" "$BASE_URL"
  hermes_set "$profile" "terminal.cwd" "$PROJECT_DIR"

  # Hermes versions differ in fallback config support. Keep this best-effort so
  # the setup stays compatible with older versions.
  hermes_set_optional "$profile" "fallback_model.provider" "$PROVIDER"
  hermes_set_optional "$profile" "fallback_model.model" "$fallback_model"
  hermes_set_optional "$profile" "fallback_model.base_url" "$BASE_URL"
}

configure_auxiliary() {
  local profile="$1"

  hermes_set "$profile" "auxiliary.kanban_decomposer.provider" "$PROVIDER"
  hermes_set "$profile" "auxiliary.kanban_decomposer.model" "$KANBAN_DECOMPOSER_MODEL"
  hermes_set "$profile" "auxiliary.kanban_decomposer.base_url" "$BASE_URL"

  hermes_set "$profile" "auxiliary.triage_specifier.provider" "$PROVIDER"
  hermes_set "$profile" "auxiliary.triage_specifier.model" "$TRIAGE_SPECIFIER_MODEL"
  hermes_set "$profile" "auxiliary.triage_specifier.base_url" "$BASE_URL"

  hermes_set "$profile" "auxiliary.profile_describer.provider" "$PROVIDER"
  hermes_set "$profile" "auxiliary.profile_describer.model" "$PROFILE_DESCRIBER_MODEL"
  hermes_set "$profile" "auxiliary.profile_describer.base_url" "$BASE_URL"

  hermes_set "$profile" "auxiliary.compression.provider" "$PROVIDER"
  hermes_set "$profile" "auxiliary.compression.model" "$SUMMARIZER_MODEL"
  hermes_set "$profile" "auxiliary.compression.base_url" "$BASE_URL"
}

main() {
  require_cmd hermes

  if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: PROJECT_DIR does not exist: $PROJECT_DIR" >&2
    exit 1
  fi

  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

  echo "Hermes Kanban profile setup"
  echo "  project dir:              $PROJECT_DIR"
  echo "  provider:                 $PROVIDER"
  echo "  base url:                 $BASE_URL"
  echo "  free primary model:       $FREE_PRIMARY_MODEL"
  echo "  cheap fallback model:     $CHEAP_FALLBACK_MODEL"
  echo "  use paid primary:         $USE_PAID_PRIMARY"
  echo "  orchestrator model:       $ORCHESTRATOR_MODEL"
  echo "  coder model:              $CODER_MODEL"
  echo "  reviewer model:           $REVIEWER_MODEL"
  echo "  researcher model:         $RESEARCHER_MODEL"
  echo "  summarizer model:         $SUMMARIZER_MODEL"
  echo "  auto decompose:           $AUTO_DECOMPOSE"
  echo

  create_profile "orchestrator" \
    "Routes work into small safe Kanban tasks, assigns specialist profiles, tracks dependencies, and avoids implementing directly unless explicitly asked. Prefer free models first and escalate only when necessary."
  create_profile "coder" \
    "Implements small software changes, reads the project carefully, runs relevant tests, and reports changed files plus verification evidence. Prefer cheap/free model usage and small bounded changes."
  create_profile "reviewer" \
    "Reviews code, tests, architecture, security, and task results; returns concise actionable findings and does not perform large implementation work. Prefer free model usage unless quality is insufficient."
  create_profile "researcher" \
    "Researches documentation, repositories, specifications, and web sources; writes grounded findings with links or concrete source references. Prefer free model usage and compact output."
  create_profile "summarizer" \
    "Compresses long logs, task histories, and worker outputs into compact handoff summaries without changing project files. Use the cheapest sufficient model."

  configure_openrouter_profile "orchestrator" "$ORCHESTRATOR_MODEL" "$CHEAP_FALLBACK_MODEL"
  configure_openrouter_profile "coder" "$CODER_MODEL" "$CHEAP_FALLBACK_MODEL"
  configure_openrouter_profile "reviewer" "$REVIEWER_MODEL" "$CHEAP_REVIEW_FALLBACK_MODEL"
  configure_openrouter_profile "researcher" "$RESEARCHER_MODEL" "$CHEAP_FALLBACK_MODEL"
  configure_openrouter_profile "summarizer" "$SUMMARIZER_MODEL" "$CHEAP_FAST_FALLBACK_MODEL"

  # Auxiliary routing matters most on the orchestrator, but setting it on all
  # profiles keeps manual profile usage predictable.
  for profile in orchestrator coder reviewer researcher summarizer; do
    configure_auxiliary "$profile"
  done

  echo "Configuring global Kanban settings"
  hermes config set "kanban.orchestrator_profile" "$ORCHESTRATOR_PROFILE"
  hermes config set "kanban.default_assignee" "$DEFAULT_ASSIGNEE"
  hermes config set "kanban.auto_decompose" "$AUTO_DECOMPOSE"
  hermes config set "kanban.auto_decompose_per_tick" "3"

  hermes config set "auxiliary.kanban_decomposer.provider" "$PROVIDER"
  hermes config set "auxiliary.kanban_decomposer.model" "$KANBAN_DECOMPOSER_MODEL"
  hermes config set "auxiliary.kanban_decomposer.base_url" "$BASE_URL"

  hermes config set "auxiliary.triage_specifier.provider" "$PROVIDER"
  hermes config set "auxiliary.triage_specifier.model" "$TRIAGE_SPECIFIER_MODEL"
  hermes config set "auxiliary.triage_specifier.base_url" "$BASE_URL"

  hermes config set "auxiliary.profile_describer.provider" "$PROVIDER"
  hermes config set "auxiliary.profile_describer.model" "$PROFILE_DESCRIBER_MODEL"
  hermes config set "auxiliary.profile_describer.base_url" "$BASE_URL"

  echo "Initializing Kanban board if needed"
  hermes kanban init || true

  cat <<EOF

Done.

Cost strategy now configured as free-first:
  Primary free model:       $FREE_PRIMARY_MODEL
  Cheap paid fallback:      $CHEAP_FALLBACK_MODEL
  Paid primary enabled:     $USE_PAID_PRIMARY

Next useful commands:
  hermes kanban list
  hermes gateway start
  hermes kanban create "Plane the next Ananta implementation step" --assignee orchestrator --workspace dir:$PROJECT_DIR
  hermes kanban create "Implement one small verified change" --assignee coder --workspace dir:$PROJECT_DIR --goal --goal-max-turns 10
  hermes kanban create "Review the latest changes" --assignee reviewer --workspace dir:$PROJECT_DIR

Cheapest mode, default:
  PROJECT_DIR=$PROJECT_DIR ./scripts/setup-hermes-kanban-profiles.sh

Focused paid mode, only when free models are too limited:
  USE_PAID_PRIMARY=true PROJECT_DIR=$PROJECT_DIR ./scripts/setup-hermes-kanban-profiles.sh

Model override example:
  FREE_PRIMARY_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
  CHEAP_FALLBACK_MODEL=minimax/minimax-m3 \
  PROJECT_DIR=$PROJECT_DIR ./scripts/setup-hermes-kanban-profiles.sh

EOF
}

main "$@"
