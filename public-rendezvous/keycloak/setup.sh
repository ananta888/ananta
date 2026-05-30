#!/usr/bin/env bash
# Richtet den Ananta-Realm in Keycloak ein.
#
# Verwendung (auf dem Server, wo docker compose läuft):
#
#   bash public-rendezvous/keycloak/setup.sh
#
# Oder direkt im Container:
#
#   docker compose -f docker-compose.public-rendezvous.yml \
#     exec keycloak bash /opt/keycloak/data/import/setup.sh
#
# Das Script ist idempotent: bereits vorhandene Objekte werden übersprungen.
# Voraussetzung: Keycloak läuft und ist erreichbar.

set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────────────────────
KC_URL="${KC_URL:-http://localhost:8080}"
KC_ADMIN="${KC_BOOTSTRAP_ADMIN_USERNAME:-${KEYCLOAK_ADMIN:-admin}}"
KC_ADMIN_PASSWORD="${KC_BOOTSTRAP_ADMIN_PASSWORD:-${KEYCLOAK_ADMIN_PASSWORD:-}}"
REALM="ananta"
CLIENT_ID="ananta-tui"

KCADM="/opt/keycloak/bin/kcadm.sh"

if [ -z "$KC_ADMIN_PASSWORD" ]; then
  echo "ERROR: KC_ADMIN_PASSWORD (oder KEYCLOAK_ADMIN_PASSWORD) muss gesetzt sein." >&2
  exit 1
fi

# ── Warten bis Keycloak bereit ist ───────────────────────────────────────────
echo "Warte auf Keycloak ($KC_URL)..."
for i in $(seq 1 30); do
  if curl -sf "$KC_URL/health/ready" >/dev/null 2>&1; then
    echo "Keycloak bereit."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Keycloak nicht erreichbar nach 30 Versuchen." >&2
    exit 1
  fi
  sleep 2
done

# ── Admin-Login ───────────────────────────────────────────────────────────────
echo "Admin-Login..."
$KCADM config credentials \
  --server "$KC_URL" \
  --realm master \
  --user "$KC_ADMIN" \
  --password "$KC_ADMIN_PASSWORD" \
  --client admin-cli

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
EXISTING_CLIENT=$($KCADM get clients -r "$REALM" --fields clientId,id \
  | grep -A1 "\"clientId\" : \"$CLIENT_ID\"" | grep '"id"' | grep -oE '"[0-9a-f-]{36}"' | tr -d '"' || true)

if [ -n "$EXISTING_CLIENT" ]; then
  echo "Client '$CLIENT_ID' existiert bereits (id=$EXISTING_CLIENT), Einstellungen werden aktualisiert..."
  CLIENT_UUID="$EXISTING_CLIENT"
  $KCADM update "clients/$CLIENT_UUID" -r "$REALM" \
    -s "enabled=true" \
    -s "publicClient=true" \
    -s "standardFlowEnabled=true" \
    -s "directAccessGrantsEnabled=false" \
    -s "attributes.\"oauth2.device.authorization.grant.enabled\"=true" \
    -s "attributes.\"oauth2.device.polling.interval\"=5" \
    -s 'redirectUris=["http://localhost:*","http://127.0.0.1:*","ananta://*"]' \
    -s 'webOrigins=["+"]'
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
    -s 'redirectUris=["http://localhost:*","http://127.0.0.1:*","ananta://*"]' \
    -s 'webOrigins=["+"]' \
    -s "fullScopeAllowed=false"
  CLIENT_UUID=$($KCADM get clients -r "$REALM" --fields clientId,id \
    | grep -A1 "\"clientId\" : \"$CLIENT_ID\"" | grep '"id"' | grep -oE '"[0-9a-f-]{36}"' | tr -d '"')
  echo "Client '$CLIENT_ID' erstellt (id=$CLIENT_UUID)."
fi

# ── Audience-Mapper (ananta-hub) ──────────────────────────────────────────────
MAPPER_NAME="ananta-hub-audience"
EXISTING_MAPPER=$($KCADM get "clients/$CLIENT_UUID/protocol-mappers/models" -r "$REALM" \
  | grep "\"$MAPPER_NAME\"" || true)

if [ -n "$EXISTING_MAPPER" ]; then
  echo "Audience-Mapper '$MAPPER_NAME' existiert bereits."
else
  echo "Erstelle Audience-Mapper '$MAPPER_NAME'..."
  $KCADM create "clients/$CLIENT_UUID/protocol-mappers/models" -r "$REALM" \
    -s "name=$MAPPER_NAME" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-audience-mapper" \
    -s "consentRequired=false" \
    -s 'config."included.custom.audience"=ananta-hub' \
    -s 'config."access.token.claim"=true' \
    -s 'config."id.token.claim"=false'
  echo "Audience-Mapper erstellt."
fi

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
echo " Audience:   ananta-hub  (im Access-Token)"
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
