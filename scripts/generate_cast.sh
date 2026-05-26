#!/usr/bin/env bash
# T05.04: Cast-Erzeugung automatisiert (CI-ready)
# Erzeugt assets/operator_tui_splash.cast + assets/operator_tui_splash.chapters.json
#
# Anforderungen:
#   - Python 3.10+
#   - .venv/bin/python (oder python3 im PATH)
#   - Ananta-Repo als Arbeitsverzeichnis
#
# Aufruf:
#   ./scripts/generate_cast.sh
#   make cast
#
# Exit 0 = Erfolg; Exit 1 = Cast zu kurz / zu groß / Skript-Fehler

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-}"
if [ -z "${PYTHON}" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    elif command -v python3 &>/dev/null; then
        PYTHON="python3"
    else
        echo "FEHLER: Python nicht gefunden. Setze PYTHON= oder lege .venv an." >&2
        exit 1
    fi
fi

OUT="${1:-assets/operator_tui_splash.cast}"
CHAPTERS_OUT="${2:-assets/operator_tui_splash.chapters.json}"

echo "=== Erklär-AI-Snake Cast-Erzeugung ==="
echo "Python:   ${PYTHON}"
echo "Ausgabe:  ${OUT}"
echo ""

START_TS=$(date +%s)

"${PYTHON}" scripts/e2e/snake_splash_e2e.py \
    --out "${OUT}" \
    --chapters-out "${CHAPTERS_OUT}"

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))

# Laufzeit-Check: max 90s
if [ "${ELAPSED}" -gt 90 ]; then
    echo "WARNUNG: Skript-Laufzeit ${ELAPSED}s überschreitet 90s-Ziel" >&2
fi

# Dateigröße-Check: max 300 KB
SIZE_KB=$(( $(wc -c < "${OUT}") / 1024 ))
if [ "${SIZE_KB}" -gt 300 ]; then
    echo "FEHLER: Cast ${SIZE_KB} KB > 300 KB Limit" >&2
    exit 1
fi

echo ""
echo "=== Fertig in ${ELAPSED}s ==="
echo "Cast:     ${OUT} (${SIZE_KB} KB)"
echo "Chapters: ${CHAPTERS_OUT}"
