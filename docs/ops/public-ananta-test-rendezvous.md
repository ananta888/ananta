# Public Ananta Test Rendezvous

This document describes the small public test infrastructure for Ananta shared-session experiments.

## Purpose

The public test profile is intended to make early Ananta TUI collaboration tests easier:

- `keycloak.ananta.de` provides OIDC identity for tests.
- `webrtc.ananta.de` provides the public rendezvous/signaling hostname.
- `stun:webrtc.ananta.de:3478` and `turn:webrtc.ananta.de:3478` provide WebRTC ICE testing via coturn.

This infrastructure is provided as a limited free test service by Peter Stuiber/ananta.de. It is not a production service, has no SLA, may be reset, rate-limited or disabled, and must not be used for confidential production workloads.

## Security warning

The public service can see metadata such as IP addresses, session IDs, timing, usernames/subjects, invite use and routing information. Chat messages, TUI view deltas and artifacts must be end-to-end encrypted before they are sent through public infrastructure.

Defaults must stay conservative:

- public profile disabled by default
- explicit opt-in required
- remote control disabled
- TUI view sharing disabled until explicitly enabled
- notes are local-only by default
- E2E payload encryption required

## Repository files

- `config/ananta_network_profiles.default.json`
- `docker/old_way/docker-compose.public-rendezvous.yml`
- `public-rendezvous/caddy/Caddyfile`
- `docs/ops/public-ananta-test-rendezvous.md`

## DNS

Both hostnames point to the same public VM:

```text
keycloak.ananta.de  A  <PUBLIC_SERVER_IP>
webrtc.ananta.de    A  <PUBLIC_SERVER_IP>
```

For the current Oracle test VM this was tested with:

```text
keycloak.ananta.de  A  79.76.105.53
webrtc.ananta.de    A  79.76.105.53
```

Update the DNS records if the public IP changes.

## Required firewall / OCI ingress

Open these inbound ports on the cloud firewall and on the VM firewall:

```text
TCP 22                 SSH
TCP 80                 Caddy HTTP / ACME
TCP 443                Caddy HTTPS / WSS
TCP 3478, UDP 3478     STUN/TURN
TCP 5349, UDP 5349     optional TURNS later
UDP 49160-49200        TURN relay range
```

For OCI Security Lists, use `Source Port Range = All` and set the service port as `Destination Port Range`.

Correct examples:

```text
UDP Source 0.0.0.0/0 Source Port All Destination Port 3478
UDP Source 0.0.0.0/0 Source Port All Destination Port 49160-49200
```

Wrong examples:

```text
UDP Source Port 3478 Destination Port All
UDP Source Port 49160-49200 Destination Port 49160-49200
```

## VM firewall

On Oracle Linux with firewalld:

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-port=3478/tcp
sudo firewall-cmd --permanent --add-port=3478/udp
sudo firewall-cmd --permanent --add-port=5349/tcp
sudo firewall-cmd --permanent --add-port=5349/udp
sudo firewall-cmd --permanent --add-port=49160-49200/udp
sudo firewall-cmd --reload
```

## Rendezvous Service

The rendezvous service (`public-rendezvous/rendezvous/`) is a standalone Flask/Gunicorn app built from source. It provides:

| Endpoint | Zweck |
|---|---|
| `GET /health` | Healthcheck |
| `GET /info` | Öffentliche Service-Infos |
| `POST /rendezvous/sessions` | Session erstellen (OIDC-Auth erforderlich) |
| `GET /rendezvous/sessions` | Eigene/beitretene Sessions listen |
| `POST /rendezvous/sessions/join` | Beitreten per Invite-Code ohne bekannte Session-ID |
| `POST /rendezvous/sessions/<id>/join` | Beitreten per Invite-Code |
| `GET /rendezvous/sessions/<id>/participants` | Presence für berechtigte Teilnehmer |
| `PATCH /rendezvous/sessions/<id>/permissions` | Session-Rechte aktualisieren (Owner) |
| `DELETE /rendezvous/sessions/<id>` | Session widerrufen (Owner) |
| `GET /rendezvous/turn-credentials` | Kurzlebige TURN-Credentials (HMAC-SHA1) |
| `POST /webrtc/sessions/<id>/signal` | SDP Offer/Answer, ICE Candidate senden |
| `GET /webrtc/sessions/<id>/signal` | Signale abholen (Polling) |
| `GET/POST /signaling` | HTTP-Polling-Alias, zukünftig native WSS |

Alle Endpunkte außer `/health` und `/info` erfordern einen gültigen Keycloak-Bearer-Token.

## Environment file

Create `.env` next to `docker/old_way/docker-compose.public-rendezvous.yml`:

```env
PUBLIC_KEYCLOAK_HOSTNAME=keycloak.ananta.de
PUBLIC_WEBRTC_HOSTNAME=webrtc.ananta.de

KEYCLOAK_DB_NAME=keycloak
KEYCLOAK_DB_USER=keycloak
KEYCLOAK_DB_PASSWORD=change_me_long_random_database_password

KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=change_me_long_random_admin_password

PUBLIC_TURN_REALM=ananta.de
PUBLIC_TURN_USER=ananta
PUBLIC_TURN_PASSWORD=change_me_long_random_turn_password
PUBLIC_TURN_EXTERNAL_IP=79.76.105.53/10.0.1.233
PUBLIC_TURN_MIN_PORT=49160
PUBLIC_TURN_MAX_PORT=49200

# TURN_SHARED_SECRET muss identisch zu coturn --static-auth-secret sein.
# Generieren: openssl rand -hex 32
TURN_SHARED_SECRET=replace_with_output_of_openssl_rand_hex_32
TURN_URLS=turn:webrtc.ananta.de:3478
TURN_TTL_SECONDS=3600
SESSION_MAX_DURATION_SECONDS=3600
RENDEZVOUS_DB_PATH=/var/lib/ananta/rendezvous.db
RENDEZVOUS_DB_TIMEOUT_SECONDS=5.0
```

For Oracle Cloud, `PUBLIC_TURN_EXTERNAL_IP` should usually be `<PUBLIC_IP>/<PRIVATE_VCN_IP>`, e.g. `79.76.105.53/10.0.1.233`.

> **TURN_SHARED_SECRET** must match the secret configured in coturn. The rendezvous service uses this to sign ephemeral TURN credentials via HMAC-SHA1 (coturn REST API format). Never commit the real secret to git.

`RENDEZVOUS_DB_PATH` points to the shared SQLite file used by all Gunicorn workers. Keep this path on a persistent Docker volume so session lists stay consistent across workers and restarts.

## Start

```bash
# Image bauen und Stack starten
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml build
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml up -d
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml ps
```

Logs:

```bash
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml logs -f caddy
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml logs -f keycloak
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml logs -f rendezvous
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml logs -f coturn
```

## Keycloak Realm Setup

Der `ananta`-Realm wird beim ersten Keycloak-Start **automatisch** aus `public-rendezvous/keycloak/ananta-realm.json` importiert (`--import-realm` Flag). Der Realm enthält:

- Self-Registration aktiviert (kein E-Mail-Verify)
- Client `ananta-tui` (public, Device Authorization Grant aktiviert)
- Audience-Mapper: jedes Token enthält `"aud": "ananta-hub"`
- Brute-Force-Schutz aktiviert
- Passwort-Policy: min. 8 Zeichen, nicht gleich Username

### Automatischer Import (Standard)

Funktioniert automatisch beim ersten `docker compose up`. Keycloak importiert den Realm wenn er noch nicht existiert.

```bash
# Keycloak-Log prüfen ob Import erfolgreich war:
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml logs keycloak | grep -i "import\|ananta"
```

Erwartete Ausgabe: `Realm 'ananta' imported`

### Manuelles Setup-Script (Fallback / Nachkonfiguration)

Falls der automatische Import fehlschlägt oder du Änderungen anwenden willst:

```bash
docker compose -f docker/old_way/docker-compose.public-rendezvous.yml exec \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=<dein-admin-passwort> \
  keycloak bash /opt/keycloak/data/import/setup.sh
```

Das Script ist **idempotent** — bestehende Objekte werden übersprungen oder aktualisiert.

### Erste Anmeldung / Registrierung

Nach dem Setup können sich User selbst registrieren:

```
https://keycloak.ananta.de/realms/ananta/account
```

Oder direkt über den Device Flow in der TUI (der öffnet den Browser automatisch).

### Realm-Konfiguration prüfen

```bash
# Realm-Status
curl -s https://keycloak.ananta.de/realms/ananta | python3 -m json.tool | grep -E '"realm"|"public_key"'

# Device-Flow-Endpunkt
curl -s https://keycloak.ananta.de/realms/ananta/.well-known/openid-configuration \
  | python3 -m json.tool | grep device
```

Erwartete Ausgabe enthält `"device_authorization_endpoint"`.

## Test DNS and HTTPS

From a client machine:

```bash
dig +short keycloak.ananta.de
dig +short webrtc.ananta.de

curl -I https://keycloak.ananta.de
curl -I https://webrtc.ananta.de
```

Expected:

- DNS returns the public server IP.
- HTTPS works with a valid Caddy/Let's Encrypt certificate.
- `webrtc.ananta.de/health` returns `{"ok": true, "service": "ananta-rendezvous"}`.

## Test Rendezvous Service

```bash
# Health
curl https://webrtc.ananta.de/health

# Service-Info
curl https://webrtc.ananta.de/info

# Token via Device Flow holen (für Tests ohne TUI):
# 1. Device Code anfordern
DEVICE=$(curl -s -X POST \
  https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/auth/device \
  -d "client_id=ananta-tui")
echo $DEVICE | python3 -m json.tool
# user_code und verification_uri ausgeben, im Browser einloggen, dann:

# 2. Token pollen bis er kommt
TOKEN=$(curl -s -X POST \
  https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/token \
  -d "client_id=ananta-tui&grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=<DEVICE_CODE>" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")

curl -H "Authorization: Bearer $TOKEN" https://webrtc.ananta.de/rendezvous/turn-credentials
```

## Test STUN/TURN

Use the WebRTC Trickle ICE test page and configure:

```text
STUN:
stun:webrtc.ananta.de:3478

TURN (statischer Test-User, nur falls PUBLIC_TURN_USER gesetzt):
turn:webrtc.ananta.de:3478
Username: ananta
Password: value of PUBLIC_TURN_PASSWORD

TURN (ephemeral via Rendezvous API):
Credentials von GET /rendezvous/turn-credentials abrufen.
```

A successful TURN test must show a `relay` candidate, for example:

```text
relay udp <PUBLIC_SERVER_IP> 49168
```

A successful STUN test shows `srflx` candidates.

Errors with code `701` are not always fatal if `relay` candidates are still gathered.

## Use from Ananta TUI

### Einmalig: ENV setzen

```bash
export ANANTA_NETWORK_PROFILE=public-ananta
```

Oder in `.env` eintragen — die TUI liest das automatisch beim Start.

### Vollständiger User-Flow (zwei Teilnehmer)

**User A — Session erstellen:**

```
# TUI starten
ananta-tui

# OIDC-Login (öffnet Browser-URL + Code in der TUI)
:oidc login

#  → Browser öffnen: https://keycloak.ananta.de/realms/ananta/device
#  → Code eingeben (wird in der TUI angezeigt)
#  → Account erstellen (Self-Registration) oder einloggen
#  → TUI empfängt Token automatisch, Status wechselt auf ✓

# Lokalen Device-Key erzeugen (einmalig)
:share key generate

# Share-Session erstellen
:share create "Meine Test Session"

# Invite-Code anzeigen (für User B)
:share invite
```

**User B — Session beitreten:**

```
# TUI starten + OIDC-Login (wie oben, anderer User)
:oidc login

# Device-Key erzeugen
:share key generate

# Invite-Code von User A eingeben
:share join <CODE>

# Status prüfen
:share status
```

**Beide sehen sich jetzt in `:share status`** und können verschlüsselt chatten.

### Derselbe Keycloak-Account in mehreren Umgebungen

Der Rendezvous-Flow trennt User-Identität und Device-Identität:

- Keycloak/OIDC authentifiziert den Account.
- Der lokale Device-Key/Fingerprint identifiziert die konkrete TUI-Umgebung.

Darum kann derselbe Account derselben Session mehrfach beitreten, zum Beispiel aus Host-TUI, Container und VM. Jede Umgebung muss einen eigenen lokalen Device-Key haben:

```
:oidc login
:share key generate
:share join <CODE>
:share status
```

Wenn ein Workspace kopiert wurde und zwei Umgebungen denselben Fingerprint anzeigen, in einer Umgebung `:share key rotate` ausführen. Danach erneut beitreten. Private Device-Keys dürfen nicht zwischen Umgebungen kopiert werden.

### Was passiert im Hintergrund

```
TUI                    keycloak.ananta.de       webrtc.ananta.de
 │                            │                        │
 │── :oidc login ────────────►│                        │
 │◄── device_code+user_code ──│                        │
 │  [User loggt sich im       │                        │
 │   Browser ein]             │                        │
 │◄── access_token ───────────│                        │
 │                            │                        │
 │── :share create ───────────│────────────────────────►│
 │                            │         POST /rendezvous/sessions
 │◄── invite_code ────────────│────────────────────────◄│
 │                            │                        │
```

Token enthält `"aud": "ananta-hub"` — der Rendezvous-Service verifiziert das gegen den Keycloak-JWKS-Endpoint.

The rendezvous service is implemented. The following features are available via `webrtc.ananta.de`:

- OIDC-authentifizierte Session-Erstellung mit Invite-Code
- Beitreten per Invite-Code (OIDC-Sub-Verifikation, Issuer-Bindung)
- Presence-Metadaten für berechtigte Teilnehmer
- Ephemere TURN-Credentials (HMAC-SHA1)
- WebRTC SDP Offer/Answer und ICE-Candidate-Relay
- HTTP-Polling unter `/signaling` (zukünftig native WebSocket)

Noch ausstehend (P2 / optional):
- Native WebSocket-Verbindungen auf `/signaling` (statt HTTP-Polling)
- Persistente Session-Speicherung (aktuell in-memory, Restart löscht Sessions)

See:

- `todos/todo.operator-tui-shared-session-oidc-device-key.json`
- `todos/todo.public-ananta-rendezvous-defaults-keycloak-webrtc.json`
