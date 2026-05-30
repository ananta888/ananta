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
- `docker-compose.public-rendezvous.yml`
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

## Environment file

Create `.env` next to `docker-compose.public-rendezvous.yml`:

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
```

For Oracle Cloud, `PUBLIC_TURN_EXTERNAL_IP` should usually be:

```text
<PUBLIC_IP>/<PRIVATE_VCN_IP>
```

Example:

```text
79.76.105.53/10.0.1.233
```

## Placeholder page

Until the real Ananta rendezvous/signaling service exists, create a small placeholder page:

```bash
mkdir -p public-rendezvous/rendezvous/html
cat > public-rendezvous/rendezvous/html/index.html <<'EOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Ananta Test Rendezvous</title>
</head>
<body>
  <h1>Ananta Test Rendezvous</h1>
  <p>Limited test endpoint for Ananta rendezvous and signaling experiments.</p>
</body>
</html>
EOF
```

## Start

```bash
docker compose -f docker-compose.public-rendezvous.yml config
docker compose -f docker-compose.public-rendezvous.yml up -d
docker compose -f docker-compose.public-rendezvous.yml ps
```

Logs:

```bash
docker compose -f docker-compose.public-rendezvous.yml logs -f caddy
docker compose -f docker-compose.public-rendezvous.yml logs -f keycloak
docker compose -f docker-compose.public-rendezvous.yml logs -f coturn
```

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
- `webrtc.ananta.de` shows the placeholder until the real signaling service is deployed.

## Test STUN/TURN

Use the WebRTC Trickle ICE test page and configure:

```text
STUN:
stun:webrtc.ananta.de:3478

TURN:
turn:webrtc.ananta.de:3478
Username: ananta
Password: value of PUBLIC_TURN_PASSWORD
```

A successful TURN test must show a `relay` candidate, for example:

```text
relay udp <PUBLIC_SERVER_IP> 49168
```

A successful STUN test shows `srflx` candidates.

Errors with code `701` are not always fatal if `relay` candidates are still gathered.

## Use from Ananta TUI

Set the public test profile explicitly:

```env
ANANTA_NETWORK_PROFILE=public-ananta
ANANTA_PUBLIC_RENDEZVOUS_ENABLED=true
ANANTA_OIDC_ISSUER=https://keycloak.ananta.de/realms/ananta
ANANTA_OIDC_CLIENT_ID=ananta-tui
ANANTA_RENDEZVOUS_URL=https://webrtc.ananta.de
ANANTA_SIGNALING_URL=wss://webrtc.ananta.de/signaling
ANANTA_TURN_URL=turn:webrtc.ananta.de:3478
ANANTA_REQUIRE_E2E_PAYLOAD_ENCRYPTION=true
```

The current repository still needs the real TUI shared-session/rendezvous runtime. The public infrastructure can already provide OIDC, HTTPS routing and TURN relay, but the following Ananta features are still implementation work:

- invite-code session creation
- `/signaling` endpoint
- WebRTC SDP/ICE exchange between TUI clients
- device-key exchange
- encrypted chat/view payload routing
- read-only shared TUI view

See:

- `todos/todo.operator-tui-shared-session-oidc-device-key.json`
- `todos/todo.public-ananta-rendezvous-defaults-keycloak-webrtc.json`
