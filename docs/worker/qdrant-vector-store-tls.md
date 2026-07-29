# Qdrant TLS provisioning

The Ananta Qdrant Compose profile enables native TLS for both REST and gRPC.
Every non-loopback endpoint must use HTTPS or gRPCS, including the private
Compose hostname `qdrant`. `trusted_private_origins` never disables this
requirement.

The profile expects three runtime files:

- a CA certificate trusted only by the Qdrant client;
- a server certificate valid for `qdrant`, `localhost` and `127.0.0.1`;
- the matching server private key.

The CA private key is needed only when issuing or rotating certificates. Keep
it outside containers and protect it as a deployment secret. Ananta loads the
CA certificate through `tls_ca_cert_ref` into a Qdrant-specific TLS context;
it does not replace the process-wide trust store.

Qdrant documents the native `service.enable_tls`, `tls.cert`, `tls.key` and
`tls.ca_cert` settings in its
[security guide](https://qdrant.tech/documentation/operations/security/).

## Standalone render proof

Compose rendering must not create placeholder secret files. This check uses
known-absent paths and proves they remain absent:

```bash
set -euo pipefail
render_prefix=/tmp/ananta-qdrant-compose-render
for suffix in api-key ca cert key; do
  rm -f "$render_prefix-$suffix"
done
ANANTA_QDRANT_API_KEY_FILE="$render_prefix-api-key" \
ANANTA_QDRANT_TLS_CA_FILE="$render_prefix-ca" \
ANANTA_QDRANT_TLS_CERT_FILE="$render_prefix-cert" \
ANANTA_QDRANT_TLS_KEY_FILE="$render_prefix-key" \
  docker compose \
    -f docker/compose-next/compose.qdrant.yml \
    --profile qdrant config --quiet
for suffix in api-key ca cert key; do
  test ! -e "$render_prefix-$suffix"
done
```

## Create local development material

The following procedure creates a deployment-local CA and a one-year server
certificate. Production environments should issue the same SANs through their
managed PKI and secret-delivery system.

```bash
set -euo pipefail
export ANANTA_QDRANT_TLS_CA_FILE="$PWD/config/secrets/qdrant-tls-ca.pem"
export ANANTA_QDRANT_TLS_CERT_FILE="$PWD/config/secrets/qdrant-tls-cert.pem"
export ANANTA_QDRANT_TLS_KEY_FILE="$PWD/config/secrets/qdrant-tls-key.pem"
export ANANTA_QDRANT_TLS_CA_KEY_FILE="$PWD/config/secrets/qdrant-tls-ca-key.pem"
install -d -m 0700 "$(dirname "$ANANTA_QDRANT_TLS_CA_FILE")"
umask 077
if test ! -s "$ANANTA_QDRANT_TLS_CA_FILE"; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -subj "/CN=Ananta local Qdrant CA" \
    -keyout "$ANANTA_QDRANT_TLS_CA_KEY_FILE" \
    -out "$ANANTA_QDRANT_TLS_CA_FILE"
fi
if test ! -s "$ANANTA_QDRANT_TLS_CERT_FILE" \
  || test ! -s "$ANANTA_QDRANT_TLS_KEY_FILE"; then
  openssl req -new -newkey rsa:3072 -sha256 -nodes \
    -subj "/CN=qdrant" \
    -addext "subjectAltName=DNS:qdrant,DNS:localhost,IP:127.0.0.1" \
    -keyout "$ANANTA_QDRANT_TLS_KEY_FILE" \
    -out /tmp/ananta-qdrant.csr
  openssl x509 -req -sha256 -days 365 \
    -in /tmp/ananta-qdrant.csr \
    -CA "$ANANTA_QDRANT_TLS_CA_FILE" \
    -CAkey "$ANANTA_QDRANT_TLS_CA_KEY_FILE" \
    -CAcreateserial -copy_extensions copy \
    -out "$ANANTA_QDRANT_TLS_CERT_FILE"
  rm -f /tmp/ananta-qdrant.csr
fi
chmod 0600 \
  "$ANANTA_QDRANT_TLS_CA_KEY_FILE" \
  "$ANANTA_QDRANT_TLS_KEY_FILE"
chmod 0644 \
  "$ANANTA_QDRANT_TLS_CA_FILE" \
  "$ANANTA_QDRANT_TLS_CERT_FILE"
openssl verify \
  -CAfile "$ANANTA_QDRANT_TLS_CA_FILE" \
  "$ANANTA_QDRANT_TLS_CERT_FILE"
openssl x509 \
  -in "$ANANTA_QDRANT_TLS_CERT_FILE" \
  -noout -checkend 604800
```

Do not distribute `qdrant-tls-ca-key.pem` to the Hub, workers or Qdrant
container. Compose mounts only the CA certificate and server certificate/key.

## Rotation

Issue the replacement certificate with the same SANs before the current
certificate expires. Verify it against the deployed CA, atomically replace
the server certificate and key in the deployment secret store, then recreate
Qdrant. Rotate the CA separately: distribute the new CA certificate and a
certificate signed by it as one change, restart Qdrant and all authorized
clients, and remove the old CA only after every client has converged.

After any rotation, run both the positive TLS request and the negative
plaintext check from the main
[Qdrant vector-store runbook](qdrant-vector-store.md). A successful
`http://127.0.0.1:6333/healthz` response is a release blocker.
