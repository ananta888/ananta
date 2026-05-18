#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_3er_lauf_lokal.sh  –  3er-Lauf mit lokalem LLM (Ollama oder LMStudio)
#
# KONFIGURATION:  config/local_llm_run.env  (einmal pro Rechner anpassen)
#
# USAGE:
#   export ANANTA_PASSWORD='<passwort>'
#   ./scripts/run_3er_lauf_lokal.sh
#
# CLI-OVERRIDES (alles optional, überschreibt Env-Datei):
#   --provider   lmstudio | ollama
#   --url        http://192.168.178.100:1234   (Provider-Base-URL)
#   --model      auto | ananta-default:latest | <modell-name>
#   --base-url   http://localhost:5000          (Ananta Hub)
#   --user       admin
#   --config-mode  goal_scoped | legacy_global_config
#   --sla        900                            (Sekunden pro Run)
#   --reset-db                                  (DB vor jedem Run leeren)
#   --out        artifacts/report.json
#   --goal-text  "..."                          (abweichende Goal-Beschreibung)
#   --planning-profile  small | medium | ""     (Planning-Anpassungen für kleine Modelle)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_DIR/config/local_llm_run.env"

# ── 1) Defaults ────────────────────────────────────────────────────────────
LOCAL_PROVIDER="${LOCAL_PROVIDER:-lmstudio}"
LOCAL_URL="${LOCAL_URL:-}"
LOCAL_MODEL="${LOCAL_MODEL:-auto}"
ANANTA_BASE_URL="${ANANTA_BASE_URL:-http://localhost:5000}"
ANANTA_USER="${ANANTA_USER:-admin}"
CONFIG_MODE="${CONFIG_MODE:-goal_scoped}"
SLA_SECONDS="${SLA_SECONDS:-900}"
RESET_DB="${RESET_DB:-0}"
GOAL_TEXT="Create a real multi-file Python project for RTX3080 eGPU utilization optimization; write README, src package, tests, run pytest, store report artifact"
OUT_FILE=""
# Planning-Policy für lokale Modelle (leer = keine Overrides, "small" = kompakte englische Prompts)
PLANNING_PROFILE="${PLANNING_PROFILE:-small}"

# ── 2) Env-Datei laden ─────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  set -a; source "$ENV_FILE"; set +a
fi

# ── 3) CLI-Argumente parsen ────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider)    LOCAL_PROVIDER="$2";   shift 2 ;;
    --url)         LOCAL_URL="$2";        shift 2 ;;
    --model)       LOCAL_MODEL="$2";      shift 2 ;;
    --base-url)    ANANTA_BASE_URL="$2";  shift 2 ;;
    --user)        ANANTA_USER="$2";      shift 2 ;;
    --config-mode) CONFIG_MODE="$2";      shift 2 ;;
    --sla)         SLA_SECONDS="$2";      shift 2 ;;
    --reset-db)    RESET_DB=1;            shift   ;;
    --out)         OUT_FILE="$2";         shift 2 ;;
    --goal-text)       GOAL_TEXT="$2";       shift 2 ;;
    --planning-profile) PLANNING_PROFILE="$2"; shift 2 ;;
    *) echo "Unbekannter Parameter: $1" >&2; exit 1 ;;
  esac
done

# ── 4) URL-Default je Provider ────────────────────────────────────────────
if [[ -z "$LOCAL_URL" ]]; then
  case "$LOCAL_PROVIDER" in
    lmstudio) LOCAL_URL="http://192.168.178.100:1234" ;;
    ollama)   LOCAL_URL="http://ollama:11434" ;;
    *)
      echo "ERROR: Unbekannter Provider '$LOCAL_PROVIDER'." >&2
      echo "       Unterstützt: ollama | lmstudio" >&2
      exit 1 ;;
  esac
fi

# ── 5) Passwort prüfen ────────────────────────────────────────────────────
if [[ -z "${ANANTA_PASSWORD:-}" ]]; then
  echo "ERROR: ANANTA_PASSWORD ist nicht gesetzt." >&2
  echo "       export ANANTA_PASSWORD='<passwort>'" >&2
  exit 1
fi

# ── 6) Ausgabe-Pfad ───────────────────────────────────────────────────────
if [[ -z "$OUT_FILE" ]]; then
  TS="$(date +%Y%m%dT%H%M%S)"
  OUT_FILE="$REPO_DIR/artifacts/3er_lauf_${LOCAL_PROVIDER}_${TS}.json"
fi
mkdir -p "$(dirname "$OUT_FILE")"

# ── 7) Scenario-JSON generieren ───────────────────────────────────────────
SCENARIO_FILE="$(mktemp /tmp/ananta_scenarios_XXXXXX.json)"
trap 'rm -f "$SCENARIO_FILE"' EXIT

export _LP="$LOCAL_PROVIDER"
export _LU="$LOCAL_URL"
export _LM="$LOCAL_MODEL"
export _PP="$PLANNING_PROFILE"

python3 - > "$SCENARIO_FILE" <<'PYEOF'
from __future__ import annotations
import json, os, sys

provider         = os.environ["_LP"].strip().lower()
url              = os.environ["_LU"].rstrip("/")
model            = os.environ["_LM"].strip()
planning_profile = os.environ.get("_PP", "").strip().lower()

SUPPORTED = {"ollama", "lmstudio"}
if provider not in SUPPORTED:
    sys.exit(f"ERROR: Provider '{provider}' nicht unterstützt. Nutze: {', '.join(sorted(SUPPORTED))}")

def _backend_patch(backend: str) -> dict:
    return {"sgpt_routing": {"task_kind_backend": {
        k: backend for k in ("coding", "analysis", "doc", "ops", "research")
    }}}

def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if (isinstance(v, dict) and isinstance(out.get(k), dict)) else v
    return out

def _llm_cfg(provider: str, url: str, model: str) -> dict:
    if provider == "ollama":
        return {
            "default_provider": "ollama",
            "default_model": model,
            "llm_config": {
                "provider": "ollama",
                "model": model,
                "base_url": f"{url}/api/generate",
            },
        }
    # lmstudio
    return {
        "default_provider": "lmstudio",
        "default_model": model,
        "llm_config": {
            "provider": "lmstudio",
            "model": model,
            "base_url": url,
            "lmstudio_api_mode": "chat",
        },
    }

# planning_policy profiles:
#   "small"  – English prompt + 512 max tokens + 400 char context cap (for small/embedded models)
#   "medium" – English prompt + 768 max tokens (balanced)
#   ""/"off" – no overrides (use Hub defaults, German prompt)
PLANNING_POLICIES = {
    "small":  {"prompt_language": "en", "max_output_tokens": 512, "context_max_chars": 400},
    "medium": {"prompt_language": "en", "max_output_tokens": 768, "context_max_chars": 600},
}

def _planning_patch(profile: str) -> dict:
    policy = PLANNING_POLICIES.get(profile)
    if not policy:
        return {}
    return {"planning_policy": policy}

llm = _llm_cfg(provider, url, model)
opencode_llm = _merge(llm, {"opencode_runtime": {"target_provider": provider}})
pp = _planning_patch(planning_profile)

scenarios = [
    {
        "id": f"opencode_{provider}_local",
        "label": f"OpenCode Worker + Local {provider.title()} ({url})",
        "config_profile": f"opencode_{provider}_local",
        "config_overrides": _merge(_merge(_backend_patch("opencode"), opencode_llm), pp),
        "config_patch":     _merge(_merge(_backend_patch("opencode"), opencode_llm), pp),
    },
    {
        "id": f"ananta_{provider}_local",
        "label": f"Ananta Worker + Local {provider.title()} ({url})",
        "config_profile": f"ananta_{provider}_local",
        "config_overrides": _merge(_merge(_backend_patch("ananta-worker"), llm), pp),
        "config_patch":     _merge(_merge(_backend_patch("ananta-worker"), llm), pp),
    },
    {
        "id": "opencode_preconfigured",
        "label": "OpenCode Worker + Preconfigured (Hub-Config)",
        "config_profile": "opencode_preconfigured",
        "config_overrides": _merge(_backend_patch("opencode"), pp),
        "config_patch":     _merge(_backend_patch("opencode"), pp),
    },
]
print(json.dumps({"scenarios": scenarios}, indent=2, ensure_ascii=False))
PYEOF

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo " 3er-Lauf lokal – ${LOCAL_PROVIDER} @ ${LOCAL_URL}"
echo " Config-Modus: ${CONFIG_MODE}  │  SLA: ${SLA_SECONDS}s"
echo " Hub: ${ANANTA_BASE_URL}  │  User: ${ANANTA_USER}"
echo " Report: ${OUT_FILE}"
echo "══════════════════════════════════════════════════════════════════════"
echo ""

# ── 8) Reset-DB-Flags zusammenbauen ──────────────────────────────────────
RESET_FLAGS=()
if [[ "$RESET_DB" == "1" ]]; then
  RESET_FLAGS=(--reset-db --i-understand-this-deletes-local-test-data)
  echo "WARNUNG: --reset-db gesetzt – Datenbank wird vor jedem Run geleert."
fi

# ── 9) Runner starten ─────────────────────────────────────────────────────
cd "$REPO_DIR"
python3 scripts/first_goal_acceptance_runner.py \
  --base-url     "$ANANTA_BASE_URL" \
  --user         "$ANANTA_USER" \
  --password     "$ANANTA_PASSWORD" \
  --config-mode  "$CONFIG_MODE" \
  --sla-seconds  "$SLA_SECONDS" \
  --scenario-file "$SCENARIO_FILE" \
  --goal-text    "$GOAL_TEXT" \
  --workspace-root "$REPO_DIR/project-workspaces" \
  --out          "$OUT_FILE" \
  "${RESET_FLAGS[@]}"

echo ""
echo "Report gespeichert: $OUT_FILE"
