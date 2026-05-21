#!/usr/bin/env bash
# Bereinigt alle bestehenden Goals und startet einen einzelnen Test-Goal-Run.
# Nutzung: ./scripts/test_single_goal.sh [goal_text]

set -e

HUB="http://localhost:5000"
GOAL="${1:-Lege ein neues Softwareprojekt an: Create a real multi-file Python Backend project for Fibonacci calculation; write README, src package, tests, run pytest, store report artifact. Erstelle einen reviewbaren Projekt-Blueprint mit Scope, Architekturvorschlag, initialem Backlog, Tests und sicheren naechsten Schritten.}"

echo "=== Bereinige bestehende Goals ==="
EXISTING=$(curl -s "$HUB/goals" | python3 -c "
import sys,json
goals = json.load(sys.stdin).get('goals') or []
for g in goals:
    print(g['id'])
")
for ID in $EXISTING; do
    echo "  Lösche Goal $ID..."
    curl -s -X DELETE "$HUB/goals/$ID" > /dev/null
done
echo "  Done. $(echo "$EXISTING" | grep -c .) Goals gelöscht."

echo ""
echo "=== Starte neuen Test-Goal ==="
RESPONSE=$(curl -s -X POST "$HUB/goals" \
  -H "Content-Type: application/json" \
  -d "{
    \"goal\": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$GOAL"),
    \"mode\": \"new_software_project\",
    \"execution_preferences\": {
      \"config_profile\": \"opencode_preconfigured\",
      \"config_overrides\": {
        \"sgpt_routing\": {
          \"task_kind_backend\": {
            \"analysis\": \"opencode\",
            \"coding\": \"opencode\",
            \"doc\": \"opencode\",
            \"ops\": \"opencode\",
            \"research\": \"opencode\"
          }
        }
      }
    }
  }")

GOAL_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['goal']['id'])")
echo "  Goal ID: $GOAL_ID"
echo ""
echo "=== Polling Status (alle 15s, max 12min) ==="
for i in $(seq 1 48); do
    DETAIL=$(curl -s "$HUB/goals/$GOAL_ID/detail")
    STATUS=$(echo "$DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['goal']['status'])" 2>/dev/null)
    echo "  $(date +%H:%M:%S) [$i/48] status=$STATUS"
    if [ "$STATUS" != "planning_running" ] && [ "$STATUS" != "pending" ]; then
        echo ""
        echo "=== Ergebnis ==="
        echo "$DETAIL" | python3 -c "
import sys,json
d=json.load(sys.stdin)['data']
g=d['goal']
pr=g.get('planning_result') or {}
tasks=d.get('tasks') or []
print('Status:         ', g.get('status'))
print('Error:          ', pr.get('error','-'))
print('Quality Reason: ', pr.get('planning_quality_reason','-'))
print('Repair Codes:   ', pr.get('selective_repair_codes', pr.get('repair_codes','-')))
print('Tasks created:  ', len(tasks))
if tasks:
    print()
    print('Tasks:')
    for t in tasks:
        print(f'  [{t.get(\"task_kind\",\"?\"):10}] {t.get(\"title\",\"?\")[:70]}')
"
        exit 0
    fi
    sleep 15
done
echo "Timeout nach 12 Minuten."
