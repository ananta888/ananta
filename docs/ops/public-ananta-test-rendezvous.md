# Public Ananta Test Rendezvous

This document describes the small public test infrastructure for Ananta shared-session experiments.

## Purpose

The public test profile supports early Angular Pair-Dev collaboration tests:

- `keycloak.ananta.de` provides OIDC identity for tests.
- `webrtc.ananta.de` provides the public rendezvous/signaling hostname.
- `stun:webrtc.ananta.de:3478` and `turn:webrtc.ananta.de:3478` provide WebRTC ICE testing via coturn.

This infrastructure is provided as a limited free test service by Peter Stuiber/ananta.de. It is not a production service, has no SLA, may be reset, rate-limited or disabled, and must not be used for confidential production workloads.

The strict public adapter always protects supported DataChannel payloads with
Pair E2EE. Public audio/video is an additive media-v2 extension for newly
created v2 sessions: both browsers must advertise the exact standard transform
capability,
verify the separately signed media contract and complete bilateral consent
before DROP-first encoded-media transforms receive keys. Old, asymmetric or
unsupported clients remain data-only. DTLS-SRTP alone is never represented as
application-level E2EE.

## Security warning

The public service can see metadata such as IP addresses, session IDs, timing, usernames/subjects, invite use and routing information. Chat messages, TUI view deltas and artifacts must be end-to-end encrypted before they are sent through public infrastructure.

Defaults must stay conservative:

- public profile disabled by default
- explicit opt-in required
- remote control disabled
- TUI view sharing disabled until explicitly enabled
- notes are local-only by default
- E2E payload encryption required

In Angular, the local/private profile is the initial state. The user must click
`Öffentlichen Pair-/WebRTC-Zugang aktivieren` before an OIDC entry point can
select and persist `public-ananta`; a Hub outage never enables it implicitly.

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
keycloak.ananta.de  A  89.168.123.128
webrtc.ananta.de    A  89.168.123.128
```

Update the DNS records if the public IP changes.

## Required firewall / OCI ingress

Open these inbound ports on the cloud firewall and on the VM firewall:

```text
TCP 22                 SSH
TCP 80                 Caddy HTTP / ACME
TCP 443                Caddy HTTPS / WSS
TCP 3478, UDP 3478     STUN/TURN
UDP 49160-49200        TURN relay range
```

TCP/UDP 5349 is intentionally closed in the supplied Compose stack. TURNS/DTLS
requires a certificate and private key mounted directly into coturn; Caddy's
HTTPS certificate cannot terminate TURN traffic.

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
| `GET /rendezvous/sessions/<id>/security/key-packages` | Adressiertes, kurzlebiges Peer-Key-Paket, strikter Basisvertrag und gegebenenfalls separat signierter Media-v2-Vertrag |
| `GET/POST /rendezvous/sessions/<id>/security/key-confirmations` | Undurchsichtige bilaterale Schlüsselbestätigung |
| `PATCH /rendezvous/sessions/<id>/permissions` | Fail-closed mit HTTP 409 `permission_update_rekey_required` |
| `DELETE /rendezvous/sessions/<id>` | Session widerrufen (Owner) |
| `GET /rendezvous/turn-credentials?session_id=<id>` | Kurzlebige TURN-Credentials für eine aktive strikte Pair-Mitgliedschaft (HMAC-SHA1) |
| `POST /webrtc/sessions/<id>/signal` | SDP Offer/Answer, ICE Candidate senden |
| `GET /webrtc/sessions/<id>/signal` | Signale abholen (Polling) |
| `GET/POST /signaling` | HTTP-Polling-Alias, zukünftig native WSS |

Alle Endpunkte außer `/health` und `/info` erfordern einen gültigen Keycloak-Bearer-Token.

Neue Angular-Clients handeln beim Erstellen und Beitreten explizit
`identity_binding_version: 2` aus. Sie senden dabei eine vor dem Request im
Tab gespeicherte 256-Bit-Capability als
`X-Ananta-Membership-Capability`. Alle späteren mitgliedsbezogenen Requests
verwenden zusätzlich den vom Server bestätigten `X-Ananta-Peer-Id`.
`X-Ananta-Device-Id` dient nur der Listen-/Reload-Erkennung und ist kein
Authentikator. Der Server speichert ausschließlich gebundene Capability-Hashes
und gibt das Geheimnis niemals zurück. Headerlose Clients und bestehende
Sessions bleiben auf Identitätsbindung v1; es gibt kein stilles Downgrade oder
Upgrade zwischen den Verträgen.

Strict-E2EE-Berechtigungen sind an den aktuellen Security-Epoch und dessen
Schlüsselmaterial gebunden. Der öffentliche Permissions-Endpunkt mutiert daher
keine Session, solange kein rekey-fähiger Client-Adapter existiert, und liefert
stabil HTTP 409 mit `reason_code=permission_update_rekey_required`.

`/signaling` is not currently a WebSocket endpoint. In particular, an OIDC
nonce is not accepted as a replacement for the required bearer token. Angular
uses OIDC-authenticated HTTP polling at the public `/webrtc/sessions/<id>/signal`
boundary. Only SDP/ICE metadata passes through that boundary. Chat, view deltas
and artifacts use the peer-to-peer encrypted DataChannel path. Ordinary video
and audio use fixed Opus/VP8 slots only when both v2 memberships negotiated the
separately authority-signed `public_media_security_contract_v2`, both peers
confirmed it over the Pair-encrypted DataChannel, and standard
`RTCRtpScriptTransform` workers are installed DROP-first. Missing support,
one-sided advertisement or any pre-key topology failure leaves the base Pair
data-only; failures after possible key release close the connection. If direct
ICE is impossible, the browser may use coturn with short-lived
credentials; coturn relays encrypted packets and does not receive application
plaintext. Media-v2 binds `ananta.public-pair.media-frame.v2`: the VP8
uncompressed prefix (10 bytes for key frames, 3 for delta frames) and the
single Opus TOC byte remain clear only so browser packetizers can preserve the
codec shape; those bytes are authenticated as AES-GCM additional data and the
remaining frame is encrypted. Public sessions never fall back to the local
Hub relay.

## Environment file

Create a root-owned environment file outside the checkout. On the public VM the
canonical location is `/etc/ananta/public-rendezvous.env`; set ownership to
`root:root` and mode `0600`. Never place the production secrets in a tracked
`.env` file.

```env
PUBLIC_KEYCLOAK_HOSTNAME=keycloak.ananta.de
PUBLIC_WEBRTC_HOSTNAME=webrtc.ananta.de

# Dedicated resource audience validated by the public Rendezvous service.
OIDC_AUDIENCE=ananta-rendezvous
OIDC_JWKS_TTL=300
OIDC_JWKS_MAX_AGE_SECONDS=600

KEYCLOAK_DB_NAME=keycloak
KEYCLOAK_DB_USER=keycloak
KEYCLOAK_DB_PASSWORD=change_me_long_random_database_password

KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=change_me_long_random_admin_password

PUBLIC_TURN_REALM=ananta.de
PUBLIC_TURN_EXTERNAL_IP=89.168.123.128/10.0.1.79
PUBLIC_TURN_MIN_PORT=49160
PUBLIC_TURN_MAX_PORT=49200

# TURN_SHARED_SECRET muss identisch zu coturn --static-auth-secret sein.
# Generieren: openssl rand -hex 32
TURN_SHARED_SECRET=replace_with_output_of_openssl_rand_hex_32
TURN_URLS=turn:webrtc.ananta.de:3478
TURN_TTL_SECONDS=600
SESSION_MAX_DURATION_SECONDS=3600
RENDEZVOUS_DB_PATH=/var/lib/ananta/rendezvous.db
RENDEZVOUS_DB_TIMEOUT_SECONDS=5.0

# The deployment workflow inserts RENDEZVOUS_SECURITY_SIGNING_SECRET here from
# the trusted host's protected seed file. Never type, print or commit that line.

# Exact browser origins permitted to call the bearer-authenticated API. These
# four values match the supported local Pair-Dev HTTP/HTTPS entry points.
CORS_ALLOWED_ORIGINS=http://127.0.0.1:4200,http://localhost:4200,https://127.0.0.1,https://localhost

# Deliberately small defaults for the 1-GiB single-node test VM.
KEYCLOAK_MEMORY_LIMIT=700m
KEYCLOAK_JAVA_OPTS_KC_HEAP=-Xms64m -Xmx320m
KEYCLOAK_DB_POOL_INITIAL_SIZE=1
KEYCLOAK_DB_POOL_MIN_SIZE=1
KEYCLOAK_DB_POOL_MAX_SIZE=10
```

For Oracle Cloud, `PUBLIC_TURN_EXTERNAL_IP` should usually be `<PUBLIC_IP>/<PRIVATE_VCN_IP>`, e.g. `89.168.123.128/10.0.1.79`.

> **TURN_SHARED_SECRET** must match the secret configured in coturn. The rendezvous service uses this to sign ephemeral TURN credentials via HMAC-SHA1 (coturn REST API format). Never commit the real secret to git.

`RENDEZVOUS_SECURITY_SIGNING_SECRET` is a separate trust boundary used to
derive the rendezvous signing identity and E2EE contract key. It is mandatory,
must contain at least 32 bytes and must not reuse the TURN secret. The service
fails closed during import when any of those rules is violated. For the public
Ananta deployment, Compose additionally pins the derived authority to
`rv:796c1b35f1815ef88b439c40`. Its private seed exists only at
`/home/krusty/.local/state/ananta-public-rendezvous/signing-secret` on the
trusted administrative host and is copied into the root-owned remote
environment by the release workflow below. Do not generate or preserve a
different seed on the public VM. An intentional authority rotation requires a
coordinated source/client pin change and new Pair sessions.

TURN credentials are issued only to a verified member of an active, complete
strict Pair session. Their 600-second validity matches coturn's maximum
allocation lifetime; clients request fresh credentials through the bound
session when needed. Coturn additionally enforces per-user/global allocation
and bandwidth quotas and denies private, link-local and multicast peer targets.

`CORS_ALLOWED_ORIGINS` is an exact comma-separated allowlist of browser origins
(`scheme://host[:port]`, without a path). Do not use `*`: bearer tokens are sent
to this API. Add a non-default Pair-Dev origin only after it is also explicitly
trusted by the corresponding Keycloak client configuration.

The supplied coturn service intentionally uses only shared-secret REST
authentication. Static `PUBLIC_TURN_USER` / `PUBLIC_TURN_PASSWORD` credentials
must not be combined with this mode: coturn treats the mechanisms as
incompatible and shared-secret authentication overrides static users.

`RENDEZVOUS_DB_PATH` points to the shared SQLite file used by all Gunicorn workers. Keep this path on a persistent Docker volume so session lists stay consistent across workers and restarts.

Rate-limit buckets use the same SQLite database so both Gunicorn workers make
one consistent admission decision. Subjects are stored only as domain-separated
SHA-256 bucket keys. Every HTTP 429 preserves the stable
`{"error":"rate_limited"}` body and adds `Retry-After` plus
`Cache-Control: no-store`; CORS exposes `Retry-After` to the Angular client. Public-v2
Create/Join retries once with the same idempotency capability, while signaling
polling pauses for the advertised interval instead of continuing to poll.

## Pre-built Keycloak image

Keycloak is augmented once in an image built away from the memory-constrained
public VM. Build the pinned amd64 image on a workstation or CI runner; do not
pass database or administrator credentials to the build:

```bash
docker buildx build --platform linux/amd64 --pull --load \
  -f public-rendezvous/keycloak/Containerfile \
  -t ananta-keycloak:26.6.1-optimized-v1 \
  public-rendezvous/keycloak

docker save ananta-keycloak:26.6.1-optimized-v1 \
  | gzip -1 > /tmp/ananta-keycloak-26.6.1-optimized-v1.tar.gz
```

Transfer the archive using the approved administrative channel, then load it on
the public VM before starting Compose:

```bash
gzip -dc /tmp/ananta-keycloak-26.6.1-optimized-v1.tar.gz \
  | sudo docker load
sudo docker image inspect ananta-keycloak:26.6.1-optimized-v1 >/dev/null
```

The archive is a generated deployment artifact and must not be committed. The
runtime Compose file deliberately has no Keycloak `build` section and uses
`pull_policy: never`; a missing local image therefore fails explicitly instead
of augmenting or pulling during a recreate. Changes to Keycloak build options or
providers require a newly versioned image and a deliberate Compose update.

## Build and release sync to `/opt/ananta`

`/opt/ananta` on the public VM is a deployed release tree, **not** a Git
checkout. Git is intentionally not required on that host. Do not run
`git pull` or build a mutable worktree there. Build the image from the exact
committed revision on a trusted workstation, label it with that revision and
transfer both the image and the matching minimal source archive.

The currently deployed public node is `x86_64`, but the target must be checked
for every replacement VM. The commands below map `x86_64` to `linux/amd64` and
`aarch64` to `linux/arm64` before building. The Rendezvous Dockerfile pins the
multi-architecture Python base manifest; Buildx selects the matching pinned
platform image rather than resolving a mutable `python:3.12-slim` tag.

```bash
(
set -Eeuo pipefail
cd /home/krusty/ananta
git fetch --prune origin main
test "$(git symbolic-ref --quiet --short HEAD)" = main || {
  echo "build must run from branch main" >&2
  exit 1
}
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || {
  echo "local main does not match origin/main" >&2
  exit 1
}
test -z "$(git status --porcelain)" || { echo "worktree is not clean" >&2; exit 1; }

signing_secret_file=/home/krusty/.local/state/ananta-public-rendezvous/signing-secret
expected_signing_key_id=rv:796c1b35f1815ef88b439c40
test -f "$signing_secret_file"
test ! -L "$signing_secret_file"
test "$(stat -c '%a' "$signing_secret_file")" = 600
test "$(stat -c '%u' "$signing_secret_file")" = "$(id -u)"
actual_signing_key_id=$(PYTHONPATH=public-rendezvous/rendezvous \
  python3 - "$signing_secret_file" <<'PY'
import sys
from pathlib import Path

from pair_security import PairSecurityAuthority

raw = Path(sys.argv[1]).read_bytes()
secret = raw.rstrip(b"\r\n")
if not secret or b"\r" in secret or b"\n" in secret or len(secret) < 32:
    raise SystemExit("protected Rendezvous signing seed is invalid")
print(PairSecurityAuthority(secret.decode("utf-8")).key_id)
PY
)
test "$actual_signing_key_id" = "$expected_signing_key_id" || {
  echo "protected Rendezvous signing seed does not match the committed authority" >&2
  exit 1
}

release_sha=$(git rev-parse --verify HEAD)
release_tree=$(mktemp -d)
trap 'rm -rf "$release_tree"' EXIT
release_archive="/tmp/ananta-public-rendezvous-${release_sha}.tar"
image_archive="/tmp/ananta-public-rendezvous-${release_sha}.image.tar.gz"
checksum_file="/tmp/ananta-public-rendezvous-${release_sha}.sha256"
ssh_key=/home/krusty/.ssh/oracle-ananta.key
test -f "$ssh_key"

remote_arch=$(ssh -i "$ssh_key" -o BatchMode=yes opc@89.168.123.128 uname -m)
case "$remote_arch" in
  x86_64) target_platform=linux/amd64 ;;
  aarch64) target_platform=linux/arm64 ;;
  *) echo "unsupported target architecture: $remote_arch" >&2; exit 1 ;;
esac

git archive --format=tar --output="$release_archive" "$release_sha" \
  docker/old_way/docker-compose.public-rendezvous.yml \
  public-rendezvous/rendezvous \
  public-rendezvous/keycloak/ananta-realm.json \
  public-rendezvous/keycloak/setup.sh
tar -xf "$release_archive" -C "$release_tree"

docker buildx build --platform "$target_platform" --load \
  --build-arg ANANTA_REVISION="$release_sha" \
  -t ananta-public-rendezvous:deployed \
  "$release_tree/public-rendezvous/rendezvous"

test "$(docker image inspect ananta-public-rendezvous:deployed \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$release_sha"
docker save ananta-public-rendezvous:deployed | gzip -1 > "$image_archive"

(
  cd /tmp
  sha256sum "$(basename "$release_archive")" "$(basename "$image_archive")" \
    > "$(basename "$checksum_file")"
)

scp -i "$ssh_key" "$release_archive" "$image_archive" "$checksum_file" \
  opc@89.168.123.128:/tmp/
# Stream the seed over SSH into a root-owned staging file. Its value is read
# from stdin, so it never appears in a command argument or command output.
ssh -i "$ssh_key" -o BatchMode=yes opc@89.168.123.128 '
  set -eu
  umask 077
  incoming=$(mktemp /tmp/ananta-rendezvous-signing-secret.XXXXXX)
  trap '\''test ! -e "$incoming" || shred --remove=unlink --zero "$incoming"'\'' EXIT
  cat >"$incoming"
  test "$(wc -c <"$incoming")" -ge 32
  sudo -n install -o root -g root -m 0600 "$incoming" \
    /etc/ananta/.public-rendezvous-signing-secret.pending
' <"$signing_secret_file"
printf 'Transferred public Rendezvous release %s\n' "$release_sha"
)
```

Use the actual administrative host/IP when it changes. This release includes
the two Keycloak realm-management files because the dedicated Rendezvous
audience is part of the cutover. Add other Caddy, Keycloak or theme paths only
when a release intentionally changes them. The external root-owned
`/etc/ananta/public-rendezvous.env` is never included in the archive or image
and must not be copied into `/opt/ananta`.

## Deploy or update Rendezvous

Set `release_sha` to the exact value used above. First verify the transferred
artifacts, back up only the source paths that will be replaced, and record the
image ID and image reference used by the running container. The immutable ID
remains the rollback target even after the `:deployed` tag moves. The backup
also includes the root-owned environment file, but never Docker volumes.

```bash
(
set -Eeuo pipefail
: "${release_sha:?export release_sha as the exact 40-character release commit}"
test "${#release_sha}" -eq 40 || {
  echo "release_sha must contain exactly 40 characters" >&2
  exit 1
}
case "$release_sha" in *[!0-9a-f]*) echo "release_sha must be lowercase hexadecimal" >&2; exit 1 ;; esac
release_archive="/tmp/ananta-public-rendezvous-${release_sha}.tar"
image_archive="/tmp/ananta-public-rendezvous-${release_sha}.image.tar.gz"
checksum_file="/tmp/ananta-public-rendezvous-${release_sha}.sha256"

(cd /tmp && sha256sum --check "$(basename "$checksum_file")")

backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/opt/ananta-deploy-backups/${backup_stamp}-${release_sha}"
compose_file=/opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml
env_file=/etc/ananta/public-rendezvous.env
signing_seed_file=/etc/ananta/.public-rendezvous-signing-secret.pending
expected_signing_key_id=rv:796c1b35f1815ef88b439c40
sudo test -f "$signing_seed_file"
test "$(sudo stat -c '%a:%U:%G' "$signing_seed_file")" = "600:root:root"

# Resolve the running target before replacing source or changing the :deployed
# tag. A missing container is not silently treated as an update.
rendezvous_container=$(sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" ps -q rendezvous)
test -n "$rendezvous_container" || {
  echo "running Rendezvous container not found; use the first-install procedure" >&2
  exit 1
}
previous_image_id=$(sudo docker inspect --format '{{.Image}}' "$rendezvous_container")
previous_image_ref=$(sudo docker inspect --format '{{.Config.Image}}' "$rendezvous_container")
rollback_image_ref="ananta-public-rendezvous:rollback-${backup_stamp}"
sudo docker image inspect "$previous_image_id" >/dev/null
sudo docker tag "$previous_image_id" "$rollback_image_ref"

sudo install -d -m 0700 "$backup_dir"
sudo tar -C /opt/ananta -cf "$backup_dir/source.tar" \
  docker/old_way/docker-compose.public-rendezvous.yml \
  public-rendezvous/rendezvous \
  public-rendezvous/keycloak/ananta-realm.json \
  public-rendezvous/keycloak/setup.sh
if sudo test -f /opt/ananta/.deployed-commit; then
  sudo cp /opt/ananta/.deployed-commit "$backup_dir/deployed-commit"
fi
sudo cp --preserve=mode,ownership,timestamps "$env_file" "$backup_dir/public-rendezvous.env"
printf '%s\n' "$previous_image_id" | sudo tee "$backup_dir/previous-image-id" >/dev/null
printf '%s\n' "$previous_image_ref" | sudo tee "$backup_dir/previous-image-ref" >/dev/null
printf '%s\n' "$rollback_image_ref" | sudo tee "$backup_dir/rollback-image-ref" >/dev/null

# Publish one complete rollback record atomically before the first mutation.
pointer_tmp=$(sudo mktemp /opt/ananta/.rendezvous-previous-backup.tmp.XXXXXX)
printf '%s\n' "$backup_dir" | sudo tee "$pointer_tmp" >/dev/null
sudo chmod 0600 "$pointer_tmp"
sudo mv "$pointer_tmp" /opt/ananta/.rendezvous-previous-backup

discard_failed_prevalidation() {
  sudo docker tag "$previous_image_id" ananta-public-rendezvous:deployed
  if sudo test -e "$signing_seed_file"; then
    sudo shred --remove=unlink --zero "$signing_seed_file"
  fi
}
restore_pre_cutover_state() {
  discard_failed_prevalidation
  sudo install -o root -g root -m 0600 \
    "$backup_dir/public-rendezvous.env" "$env_file"
  sudo tar -C /opt/ananta -xf "$backup_dir/source.tar"
}
pre_cutover_pending=0
rollback_pre_cutover_on_exit() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$pre_cutover_pending" -eq 1 ]; then
    set +e
    restore_pre_cutover_state
    restore_status=$?
    if [ "$restore_status" -ne 0 ]; then
      echo "automatic pre-cutover restore failed; use the recorded rollback" >&2
    fi
  fi
  exit "$status"
}
trap rollback_pre_cutover_on_exit EXIT

# Load the exact new image, then derive its public authority from the staged
# seed in an isolated, read-only container. Only the non-secret key ID is kept
# in shell memory. A mismatch stops before the external environment changes;
# the running container still references its previously recorded image ID.
if ! gzip -dc "$image_archive" | sudo docker load; then
  discard_failed_prevalidation
  echo "Rendezvous image load failed; previous image tag restored" >&2
  exit 1
fi
if ! actual_signing_key_id=$(sudo docker run --rm --network none --read-only \
    --mount type=bind,src="$signing_seed_file",dst=/run/ananta-signing-seed,readonly \
    --entrypoint python3 ananta-public-rendezvous:deployed -c \
    'from pathlib import Path; from pair_security import PairSecurityAuthority; secret=Path("/run/ananta-signing-seed").read_text(encoding="utf-8").strip(); print(PairSecurityAuthority(secret).key_id)'); then
  discard_failed_prevalidation
  echo "transferred Rendezvous signing seed could not be validated" >&2
  exit 1
fi
test "$actual_signing_key_id" = "$expected_signing_key_id" || {
  discard_failed_prevalidation
  echo "transferred Rendezvous signing seed does not match the committed authority" >&2
  exit 1
}

# Migrate the external environment atomically from the trusted, root-owned
# staged seed. The secret remains in files or shell memory and is never passed
# as a command argument or written to stdout.
if ! sudo sh -c '
  set -eu
  env_file=$1
  seed_file=$2
  signing=$(cat "$seed_file")
  turn=$(sed -n "s/^TURN_SHARED_SECRET=//p" "$env_file" | tail -n 1)
  [ "${#signing}" -ge 32 ] || { echo "trusted signing seed is too short" >&2; exit 1; }
  [ "$signing" != "$turn" ] || { echo "signing secret reuses TURN secret" >&2; exit 1; }
  tmp=$(mktemp "${env_file}.tmp.XXXXXX")
  trap '\''test ! -e "$tmp" || shred --remove=unlink --zero "$tmp"'\'' EXIT
  awk '\''! /^(OIDC_AUDIENCE|OIDC_JWKS_TTL|OIDC_JWKS_MAX_AGE_SECONDS|CORS_ALLOWED_ORIGINS|RENDEZVOUS_SECURITY_SIGNING_SECRET|TURN_TTL_SECONDS)=/'\'' \
    "$env_file" >"$tmp"
  printf "%s\n" \
    "OIDC_AUDIENCE=ananta-rendezvous" \
    "OIDC_JWKS_TTL=300" \
    "OIDC_JWKS_MAX_AGE_SECONDS=600" \
    "CORS_ALLOWED_ORIGINS=http://127.0.0.1:4200,http://localhost:4200,https://127.0.0.1,https://localhost" \
    "TURN_TTL_SECONDS=600" \
    "RENDEZVOUS_SECURITY_SIGNING_SECRET=$signing" >>"$tmp"
  install -o root -g root -m 0600 "$tmp" "$env_file"
  shred --remove=unlink --zero "$seed_file"
' sh "$env_file" "$signing_seed_file"; then
  restore_pre_cutover_state
  echo "Rendezvous environment migration failed; pre-cutover state restored" >&2
  exit 1
fi
pre_cutover_pending=1

sudo tar --no-same-owner -xf "$release_archive" -C /opt/ananta

# Existing realms are not changed by `--import-realm`. Apply the two additive
# ananta-tui audiences explicitly while the old Rendezvous remains online.
sudo docker compose -p ananta-public \
  --env-file /etc/ananta/public-rendezvous.env \
  -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
  ps keycloak
sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  exec -T keycloak bash /opt/keycloak/data/import/setup.sh
pre_cutover_pending=0
)
```

Access tokens issued before the setup run contain only `ananta-hub`; they will
be rejected by the new Rendezvous service. The infrastructure cutover may
continue after `setup.sh` succeeds, but every Pair-Dev/TUI client must perform a
fresh login before its next public Pair session. The new Access Token must
contain both `ananta-hub` and `ananta-rendezvous` in `aud`. Do not paste or log
the token. Existing Hub calls remain compatible because the Hub audience was
not removed.

```bash
(
set -Eeuo pipefail
: "${release_sha:?export the same exact 40-character release commit}"
release_archive="/tmp/ananta-public-rendezvous-${release_sha}.tar"
image_archive="/tmp/ananta-public-rendezvous-${release_sha}.image.tar.gz"
checksum_file="/tmp/ananta-public-rendezvous-${release_sha}.sha256"
compose_file=/opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml
env_file=/etc/ananta/public-rendezvous.env
expected_signing_key_id=rv:796c1b35f1815ef88b439c40
backup_dir=$(sudo cat /opt/ananta/.rendezvous-previous-backup)
sudo test -f "$backup_dir/source.tar"
sudo test -f "$backup_dir/public-rendezvous.env"

# Once either public service is recreated, every later gate is a deployment
# transaction boundary. A failed health, revision, CORS, route or TURN check
# restores the exact recorded source, environment and image before returning
# the original failure status.
cutover_started=0
rollback_failed_cutover_on_exit() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$cutover_started" -eq 1 ]; then
    set +e
    previous_image_id=$(sudo cat "$backup_dir/previous-image-id")
    previous_image_ref=$(sudo cat "$backup_dir/previous-image-ref")
    rollback_image_ref=$(sudo cat "$backup_dir/rollback-image-ref")
    sudo docker image inspect "$rollback_image_ref" >/dev/null &&
      test "$(sudo docker image inspect "$rollback_image_ref" --format '{{.Id}}')" = "$previous_image_id" &&
      sudo tar -C /opt/ananta -xf "$backup_dir/source.tar" &&
      sudo install -o root -g root -m 0600 \
        "$backup_dir/public-rendezvous.env" "$env_file" &&
      sudo docker tag "$rollback_image_ref" "$previous_image_ref" &&
      sudo docker compose -p ananta-public \
        --env-file "$env_file" \
        -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
        up -d --no-build --no-deps --force-recreate rendezvous coturn &&
      curl --fail --silent --show-error --retry 20 --retry-delay 2 \
        https://webrtc.ananta.de/health &&
      sudo ss -lun | grep -Eq '(^|[[:space:]])[^[:space:]]*:3478[[:space:]]'
    rollback_status=$?
    if [ "$rollback_status" -eq 0 ]; then
      if sudo test -f "$backup_dir/deployed-commit"; then
        sudo cp "$backup_dir/deployed-commit" /opt/ananta/.deployed-commit
      else
        sudo rm -f /opt/ananta/.deployed-commit
      fi
      echo "failed public cutover was rolled back automatically" >&2
    else
      echo "automatic public cutover rollback failed; use the recorded manual rollback" >&2
    fi
  fi
  exit "$status"
}
trap rollback_failed_cutover_on_exit EXIT

test "$(sudo docker image inspect ananta-public-rendezvous:deployed \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$release_sha"

sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  config --quiet

# Clean-image smoke: validates secrets, packaged modules and the health contract
# without contacting Keycloak or touching the persistent rendezvous database.
sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  run --rm --no-deps -e RENDEZVOUS_DB_PATH=/tmp/rendezvous-smoke.db \
  rendezvous python3 -c \
  "import app, config, service; assert config.RENDEZVOUS_EXPECTED_SIGNING_KEY_ID == '$expected_signing_key_id'; assert service._SECURITY_AUTHORITY.key_id == '$expected_signing_key_id'; r=app.app.test_client().get('/health'); assert r.status_code == 200 and r.get_json().get('ok') is True"

cutover_started=1
sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  up -d --no-build --no-deps --force-recreate rendezvous coturn
curl --fail --silent --show-error --retry 20 --retry-delay 2 \
  https://webrtc.ananta.de/health

# Prove that the recreated container, the exact CORS contract and the new
# security route are live before publishing the deployment marker.
rendezvous_container=$(sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" ps -q rendezvous)
coturn_container=$(sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" ps -q coturn)
test -n "$rendezvous_container"
test -n "$coturn_container"
test "$(sudo docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
  "$rendezvous_container")" = "$release_sha"
test "$(sudo docker inspect --format '{{.RestartCount}}' "$rendezvous_container")" = 0
test "$(sudo docker inspect --format '{{.RestartCount}}' "$coturn_container")" = 0
sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  exec -T rendezvous python3 -c \
  "import config, service; assert config.RENDEZVOUS_EXPECTED_SIGNING_KEY_ID == '$expected_signing_key_id'; assert service._SECURITY_AUTHORITY.key_id == '$expected_signing_key_id'"
coturn_args=$(sudo docker inspect --format '{{json .Args}}' "$coturn_container")
case "$coturn_args" in *'--user-quota=4'*'--total-quota=32'*'--no-multicast-peers'*) ;; \
  *) echo "coturn hardening arguments are not active" >&2; exit 1 ;; esac
sudo ss -lun | grep -Eq '(^|[[:space:]])[^[:space:]]*:3478[[:space:]]'
preflight_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X OPTIONS -H 'Origin: http://localhost:4200' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: Authorization, X-Ananta-Device-Id, X-Ananta-Peer-Id, X-Ananta-Membership-Capability' \
  https://webrtc.ananta.de/rendezvous/sessions)
case "$preflight_status" in 200|204) ;; *) echo "unexpected preflight status: $preflight_status" >&2; exit 1 ;; esac
test "$(curl --silent --dump-header - --output /dev/null \
  -X OPTIONS -H 'Origin: http://localhost:4200' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: Authorization, X-Ananta-Device-Id, X-Ananta-Peer-Id, X-Ananta-Membership-Capability' \
  https://webrtc.ananta.de/rendezvous/sessions \
  | tr -d '\r' | awk -F ': ' 'tolower($1)=="access-control-allow-origin" { print $2 }')" \
  = 'http://localhost:4200'
preflight_allowed_headers=$(curl --silent --dump-header - --output /dev/null \
  -X OPTIONS -H 'Origin: http://localhost:4200' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: Authorization, X-Ananta-Device-Id, X-Ananta-Peer-Id, X-Ananta-Membership-Capability' \
  https://webrtc.ananta.de/rendezvous/sessions \
  | tr -d '\r' | awk -F ': ' 'tolower($1)=="access-control-allow-headers" { print tolower($2) }')
for required_header in authorization x-ananta-device-id x-ananta-peer-id x-ananta-membership-capability; do
  case ",$preflight_allowed_headers," in
    *", $required_header,"*|*",$required_header,"*) ;;
    *) echo "missing CORS header allowance: $required_header" >&2; exit 1 ;;
  esac
done
test -z "$(curl --silent --dump-header - --output /dev/null \
  -X OPTIONS -H 'Origin: https://untrusted.invalid' \
  -H 'Access-Control-Request-Method: GET' \
  https://webrtc.ananta.de/rendezvous/sessions \
  | tr -d '\r' | awk -F ': ' 'tolower($1)=="access-control-allow-origin" { print $2 }')"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  https://webrtc.ananta.de/rendezvous/sessions/probe/security/key-packages)" = 401

sudo docker compose -p ananta-public \
  --env-file "$env_file" -f "$compose_file" \
  ps rendezvous coturn
printf '%s\n' "$release_sha" | sudo tee /opt/ananta/.deployed-commit >/dev/null
cutover_started=0

sudo rm -f "$release_archive" "$image_archive" "$checksum_file"
)
```

After the remote health check succeeds, remove the local generated artifacts:

```bash
set -Eeuo pipefail
: "${release_sha:?export the deployed 40-character release commit}"
release_archive="/tmp/ananta-public-rendezvous-${release_sha}.tar"
image_archive="/tmp/ananta-public-rendezvous-${release_sha}.image.tar.gz"
checksum_file="/tmp/ananta-public-rendezvous-${release_sha}.sha256"
rm -f "$release_archive" "$image_archive" "$checksum_file"
```

For the first start of the complete stack, load both pre-built images first and
then start it in stages with `--no-build`: PostgreSQL and Keycloak, Rendezvous
and coturn, then Caddy. Check `docker compose ps` and logs after every stage.

### Roll back Rendezvous by image ID

If the new container fails after the smoke check, restore the exact previous
image instead of rebuilding source on the server:

```bash
(
set -Eeuo pipefail
backup_dir=$(sudo cat /opt/ananta/.rendezvous-previous-backup)
sudo test -f "$backup_dir/source.tar"
sudo test -f "$backup_dir/public-rendezvous.env"
previous_image_id=$(sudo cat "$backup_dir/previous-image-id")
previous_image_ref=$(sudo cat "$backup_dir/previous-image-ref")
rollback_image_ref=$(sudo cat "$backup_dir/rollback-image-ref")
sudo docker image inspect "$rollback_image_ref" >/dev/null
test "$(sudo docker image inspect "$rollback_image_ref" --format '{{.Id}}')" = "$previous_image_id"
sudo tar -C /opt/ananta -xf "$backup_dir/source.tar"
sudo install -o root -g root -m 0600 \
  "$backup_dir/public-rendezvous.env" /etc/ananta/public-rendezvous.env
sudo docker tag "$rollback_image_ref" "$previous_image_ref"
sudo docker compose -p ananta-public \
  --env-file /etc/ananta/public-rendezvous.env \
  -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
  up -d --no-build --no-deps --force-recreate rendezvous coturn
curl --fail --silent --show-error --retry 20 --retry-delay 2 \
  https://webrtc.ananta.de/health
sudo ss -lun | grep -Eq '(^|[[:space:]])[^[:space:]]*:3478[[:space:]]'
if sudo test -f "$backup_dir/deployed-commit"; then
  sudo cp "$backup_dir/deployed-commit" /opt/ananta/.deployed-commit
fi
)
```

Do not use `docker compose down -v`: the named PostgreSQL, Caddy and rendezvous
volumes contain persistent state. The SELinux `Z` labels on the two read-only
bind mounts are required for enforcing Oracle Linux 9 hosts.

Logs:

```bash
sudo docker compose -p ananta-public \
  --env-file /etc/ananta/public-rendezvous.env \
  -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
  logs -f caddy keycloak rendezvous coturn
```

## Keycloak Realm Setup

Der `ananta`-Realm wird beim ersten Keycloak-Start **automatisch** aus `public-rendezvous/keycloak/ananta-realm.json` importiert (`--import-realm` Flag). Der Realm enthält:

- Self-Registration aktiviert (kein E-Mail-Verify)
- Client `ananta-tui` (public, Device Authorization Grant aktiviert)
- Additive Audience-Mapper: Access-Tokens enthalten `ananta-hub` für den Hub
  und `ananta-rendezvous` für den öffentlichen Rendezvous-Dienst
- Brute-Force-Schutz aktiviert
- Passwort-Policy: min. 8 Zeichen, nicht gleich Username

### Automatischer Import (Standard)

Funktioniert automatisch beim ersten `docker compose up`. Keycloak importiert den Realm wenn er noch nicht existiert.

```bash
# Keycloak-Log prüfen ob Import erfolgreich war:
sudo docker compose -p ananta-public \
  --env-file /etc/ananta/public-rendezvous.env \
  -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
  logs keycloak | grep -i "import\|ananta"
```

Erwartete Ausgabe: `Realm 'ananta' imported`

### Manuelles Setup-Script (Fallback / Nachkonfiguration)

Falls der automatische Import fehlschlägt oder du Änderungen anwenden willst:

```bash
sudo docker compose -p ananta-public \
  --env-file /etc/ananta/public-rendezvous.env \
  -f /opt/ananta/docker/old_way/docker-compose.public-rendezvous.yml \
  exec \
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

# Die Session vorher mit Angular Pair-Dev erstellen bzw. ihr beitreten.
SESSION_ID=<active-strict-pair-session-uuid>
curl -H "Authorization: Bearer $TOKEN" \
  "https://webrtc.ananta.de/rendezvous/turn-credentials?session_id=${SESSION_ID}"
```

## Test STUN/TURN

Use the WebRTC Trickle ICE test page and configure:

```text
STUN:
stun:webrtc.ananta.de:3478

TURN (ephemeral via Rendezvous API):
Credentials von GET /rendezvous/turn-credentials?session_id=<aktive-pair-id>
abrufen. Der eingeloggte Peer muss aktuelles Mitglied der vollständigen
strict-E2EE-Pair-Session sein.
```

A successful TURN test must show a `relay` candidate, for example:

```text
relay udp <PUBLIC_SERVER_IP> 49168
```

A successful STUN test shows `srflx` candidates.

Errors with code `701` are not always fatal if `relay` candidates are still gathered.

## Supported client scope

The strict public Pair flow is currently supported by the Angular Pair-Dev
surface. The operator TUI can use the public Keycloak Device Flow for login,
but its current `:share create` and `:share join` payloads do not yet provide
the P-256 ECDH key material required by this Rendezvous contract. Those TUI
commands must therefore not be used against `webrtc.ananta.de` until the TUI
gets its own strict Pair adapter. The server rejects legacy or downgraded
sessions rather than silently weakening E2EE.

A public v2 session has exactly two distinct device peers. Both computers may
use the same Keycloak account because account authorization and device/E2EE
addressing are separate. Each browser environment must retain its own local
P-256 private key and tab-scoped membership capability; copied device keys are
rejected. Existing v1 sessions cannot be upgraded in place, so create a new
Pair-Dev session after deploying v2. Two different Keycloak accounts remain
supported unchanged.

### Was passiert im Hintergrund

```
Angular Pair-Dev       keycloak.ananta.de       webrtc.ananta.de
 │                            │                        │
 │── Authorization+PKCE ─────►│                        │
 │  [User loggt sich im       │                        │
 │   Browser ein]             │                        │
 │◄── access_token ───────────│                        │
 │                            │                        │
 │── Pair erstellen ──────────│────────────────────────►│
 │                            │         POST /rendezvous/sessions
 │◄── invite_code ────────────│────────────────────────◄│
 │                            │                        │
```

Das Access-Token enthält beide Resource-Audiences. Der Rendezvous-Service
verifiziert ausschließlich `ananta-rendezvous` gegen den Keycloak-JWKS-Endpunkt;
`ananta-hub` bleibt unabhängig davon für bestehende Hub-Aufrufe erhalten.

The rendezvous service is implemented. The following features are available via `webrtc.ananta.de`:

- OIDC-authentifizierte Session-Erstellung mit Invite-Code
- Beitreten per Invite-Code (OIDC-Sub-Verifikation, Issuer-Bindung)
- Presence-Metadaten für berechtigte Teilnehmer
- Rendezvous-signierte, adressierte Peer-Key-Pakete und bilaterale E2EE-Bestätigung
- Ephemere TURN-Credentials (HMAC-SHA1)
- WebRTC SDP Offer/Answer und ICE-Candidate-Relay
- HTTP-Polling unter `/signaling` (zukünftig native WebSocket)
- Direkte Browser-zu-Browser-Nutzdaten; TURN nur als ICE-Fallback, kein Hub-Relay

Noch ausstehend (P2 / optional):
- Native WebSocket-Verbindungen auf `/signaling` (statt HTTP-Polling)

Session-, Teilnehmer- und Signaling-Zustand wird bereits persistent in der
SQLite-Datei auf `public_rendezvous_data` gespeichert. Ein Container-Neustart
löscht diese Daten nicht.

See:

- `todos/todo.operator-tui-shared-session-oidc-device-key.json`
- `todos/todo.public-ananta-rendezvous-defaults-keycloak-webrtc.json`
