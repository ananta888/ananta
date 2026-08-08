#!/usr/bin/env bash
# Richtet den Ananta-Realm in Keycloak ein.
#
# Verwendung (auf dem Server, wo docker compose läuft):
#
#   bash public-rendezvous/keycloak/setup.sh
#
# Oder direkt im Container:
#
#   docker compose -f docker/old_way/docker-compose.public-rendezvous.yml \
#     exec keycloak bash /opt/keycloak/data/import/setup.sh
#
# Das Script ist idempotent: bereits vorhandene Objekte werden angeglichen.
# Voraussetzung: Keycloak läuft und ist erreichbar.

set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────────────────────
KC_URL="${KC_URL:-http://localhost:8080}"
KC_ADMIN="${KC_BOOTSTRAP_ADMIN_USERNAME:-${KEYCLOAK_ADMIN:-admin}}"
KC_ADMIN_PASSWORD="${KC_BOOTSTRAP_ADMIN_PASSWORD:-${KEYCLOAK_ADMIN_PASSWORD:-}}"
REALM="ananta"
CLIENT_ID="ananta-tui"

KCADM="${KCADM:-/opt/keycloak/bin/kcadm.sh}"

if [ -z "$KC_ADMIN_PASSWORD" ]; then
  echo "ERROR: KC_ADMIN_PASSWORD (oder KEYCLOAK_ADMIN_PASSWORD) muss gesetzt sein." >&2
  exit 1
fi

# ── Auf Keycloak warten und Admin-Login durchführen ───────────────────────────
# Das schlanke Keycloak-Image enthält absichtlich kein curl. Der kcadm-Login
# prüft zugleich die Admin-API und vermeidet eine zweite Health-Check-Abhängigkeit.
echo "Warte auf Keycloak und Admin-API ($KC_URL)..."
for i in $(seq 1 30); do
  if "$KCADM" config credentials \
    --server "$KC_URL" \
    --realm master \
    --user "$KC_ADMIN" \
    --password "$KC_ADMIN_PASSWORD" \
    --client admin-cli >/dev/null 2>&1; then
    echo "Keycloak bereit, Admin-Login erfolgreich."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Keycloak-Admin-API nicht erreichbar oder Zugang ungültig nach 30 Versuchen." >&2
    exit 1
  fi
  sleep 2
done

# ── Realm erstellen (falls nicht vorhanden) ───────────────────────────────────
if $KCADM get realms/$REALM >/dev/null 2>&1; then
  echo "Realm '$REALM' existiert bereits, wird übersprungen."
else
  echo "Erstelle Realm '$REALM'..."
  $KCADM create realms \
    -s "realm=$REALM" \
    -s "enabled=true" \
    -s "displayName=Ananta" \
    -s "registrationAllowed=true" \
    -s "loginWithEmailAllowed=true" \
    -s "duplicateEmailsAllowed=false" \
    -s "resetPasswordAllowed=true" \
    -s "verifyEmail=false" \
    -s "rememberMe=true" \
    -s "bruteForceProtected=true" \
    -s "sslRequired=external" \
    -s "accessTokenLifespan=3600" \
    -s "oauth2DeviceCodeLifespan=600" \
    -s "oauth2DevicePollingInterval=5" \
    -s 'passwordPolicy=length(8) and notUsername(undefined)'
  echo "Realm '$REALM' erstellt."
fi

# ── Client erstellen (falls nicht vorhanden) ──────────────────────────────────
find_client_id() {
  "$KCADM" get clients -r "$REALM" --fields id,clientId \
    | awk -v expected="$CLIENT_ID" '
        /\{/ { resource_id = ""; client_id = "" }
        /"id"[[:space:]]*:/ {
          resource_id = $0
          sub(/^.*"id"[[:space:]]*:[[:space:]]*"/, "", resource_id)
          sub(/".*$/, "", resource_id)
        }
        /"clientId"[[:space:]]*:/ {
          client_id = $0
          sub(/^.*"clientId"[[:space:]]*:[[:space:]]*"/, "", client_id)
          sub(/".*$/, "", client_id)
        }
        /\}/ {
          if (client_id == expected) {
            print resource_id
            exit
          }
        }
      '
}

EXISTING_CLIENT="$(find_client_id)"

if [ -n "$EXISTING_CLIENT" ]; then
  echo "Client '$CLIENT_ID' existiert bereits (id=$EXISTING_CLIENT), Einstellungen werden aktualisiert..."
  CLIENT_UUID="$EXISTING_CLIENT"
  $KCADM update "clients/$CLIENT_UUID" -r "$REALM" \
    -s "enabled=true" \
    -s "publicClient=true" \
    -s "standardFlowEnabled=true" \
    -s "directAccessGrantsEnabled=false" \
    -s "fullScopeAllowed=false" \
    -s "attributes.\"oauth2.device.authorization.grant.enabled\"=true" \
    -s "attributes.\"oauth2.device.polling.interval\"=5" \
    -s "attributes.\"pkce.code.challenge.method\"=S256" \
    -s 'redirectUris=["http://localhost:*","http://127.0.0.1:*","https://localhost/oidc-callback","https://127.0.0.1/oidc-callback","ananta://*"]' \
    -s 'webOrigins=["http://localhost:4200","http://127.0.0.1:4200","https://localhost","https://127.0.0.1"]'
else
  echo "Erstelle Client '$CLIENT_ID'..."
  $KCADM create clients -r "$REALM" \
    -s "clientId=$CLIENT_ID" \
    -s 'name=Ananta TUI' \
    -s "enabled=true" \
    -s "publicClient=true" \
    -s "standardFlowEnabled=true" \
    -s "directAccessGrantsEnabled=false" \
    -s "attributes.\"oauth2.device.authorization.grant.enabled\"=true" \
    -s "attributes.\"oauth2.device.polling.interval\"=5" \
    -s "attributes.\"pkce.code.challenge.method\"=S256" \
    -s 'redirectUris=["http://localhost:*","http://127.0.0.1:*","https://localhost/oidc-callback","https://127.0.0.1/oidc-callback","ananta://*"]' \
    -s 'webOrigins=["http://localhost:4200","http://127.0.0.1:4200","https://localhost","https://127.0.0.1"]' \
    -s "fullScopeAllowed=false"
  CLIENT_UUID="$(find_client_id)"
  if [ -z "$CLIENT_UUID" ]; then
    echo "ERROR: Client '$CLIENT_ID' wurde erstellt, konnte aber nicht erneut geladen werden." >&2
    exit 1
  fi
  echo "Client '$CLIENT_ID' erstellt (id=$CLIENT_UUID)."
fi

# ── Additive Access-Token-Audiences ───────────────────────────────────────────
# ananta-hub bleibt für bestehende Hub-Aufrufer erhalten. Der öffentliche
# Rendezvous-Dienst akzeptiert ausschließlich die dedizierte Audience.
find_audience_mapper_id() {
  local mapper_name="$1"

  "$KCADM" get "clients/$CLIENT_UUID/protocol-mappers/models" -r "$REALM" \
    --fields id,name \
    | awk -v expected="$mapper_name" '
        /\{/ { mapper_id = ""; mapper_name = "" }
        /"id"[[:space:]]*:/ {
          mapper_id = $0
          sub(/^.*"id"[[:space:]]*:[[:space:]]*"/, "", mapper_id)
          sub(/".*$/, "", mapper_id)
        }
        /"name"[[:space:]]*:/ {
          mapper_name = $0
          sub(/^.*"name"[[:space:]]*:[[:space:]]*"/, "", mapper_name)
          sub(/".*$/, "", mapper_name)
        }
        /\}/ {
          if (mapper_name == expected) {
            print mapper_id
            exit
          }
        }
      '
}

upsert_audience_mapper() {
  local mapper_name="$1"
  local audience="$2"
  local mapper_id
  local mapper_collection="clients/$CLIENT_UUID/protocol-mappers/models"
  local -a desired=(
    -s "name=$mapper_name"
    -s "protocol=openid-connect"
    -s "protocolMapper=oidc-audience-mapper"
    -s "consentRequired=false"
    -s "config.\"included.custom.audience\"=$audience"
    -s 'config."access.token.claim"=true'
    -s 'config."id.token.claim"=false'
  )

  mapper_id="$(find_audience_mapper_id "$mapper_name")"
  if [ -n "$mapper_id" ]; then
    echo "Aktualisiere Audience-Mapper '$mapper_name'..."
    "$KCADM" update "$mapper_collection/$mapper_id" -r "$REALM" "${desired[@]}"
  else
    echo "Erstelle Audience-Mapper '$mapper_name'..."
    "$KCADM" create "$mapper_collection" -r "$REALM" "${desired[@]}"
  fi
}

upsert_audience_mapper "ananta-hub-audience" "ananta-hub"
upsert_audience_mapper "ananta-rendezvous-audience" "ananta-rendezvous"

# ── Realm-Rolle 'ananta-user' ─────────────────────────────────────────────────
EXISTING_ROLE=$($KCADM get roles -r "$REALM" --fields name \
  | grep '"ananta-user"' || true)
if [ -n "$EXISTING_ROLE" ]; then
  echo "Rolle 'ananta-user' existiert bereits."
else
  echo "Erstelle Rolle 'ananta-user'..."
  $KCADM create roles -r "$REALM" \
    -s "name=ananta-user" \
    -s "description=Standard Ananta user"
  echo "Rolle erstellt."
fi

# ── Default-Rolle setzen ──────────────────────────────────────────────────────
echo "Setze 'ananta-user' als Default-Realm-Rolle..."
$KCADM add-roles -r "$REALM" \
  --rname "default-roles-$REALM" \
  --rolename "ananta-user" 2>/dev/null || true

# ── Zusammenfassung ────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo " Ananta Keycloak Setup abgeschlossen"
echo "════════════════════════════════════════════════════"
echo ""
echo " Realm:      $REALM"
echo " Client:     $CLIENT_ID  (public, Device Grant ON)"
echo " Registrierung: aktiviert (kein E-Mail-Verify)"
echo " Audiences:  ananta-hub, ananta-rendezvous  (im Access-Token)"
echo ""
echo " Nächste Schritte:"
echo "   1. Öffne https://keycloak.ananta.de/realms/ananta/account"
echo "      und registriere dich als erster User."
echo "   2. Starte in der TUI:"
echo "      ANANTA_NETWORK_PROFILE=public-ananta ananta-tui"
echo "      :oidc login"
echo ""
echo " Device-Flow-Endpunkt:"
echo "   https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/auth/device"
echo ""
