# WebRTC room edge

`webrtc.ananta.de` preserves the Ananta rendezvous API on the public host and
routes every other path to the private room application on the Mini-PC. The
room application is deliberately not part of the Ananta Hub/Worker process and
must expose HTTP on port `8080` as the container `webrtc-room-server`.

## Automated preflight

Provide absolute paths to a certificate and a mode-`0600` private key, then run:

```bash
export ANANTA_WEBRTC_TLS_CERT_FILE=/srv/ananta-secrets/webrtc.ananta.de.crt
export ANANTA_WEBRTC_TLS_KEY_FILE=/srv/ananta-secrets/webrtc.ananta.de.key
python scripts/provision_webrtc_room_edge.py --apply
```

The command validates the certificate, refuses symlinked or broadly readable
key material, creates `webrtc-edge` when absent and idempotently attaches the
existing room container. It never starts an unpinned third-party image.

Start the Ananta development edge only after the preflight succeeds. Compose
mounts both TLS files read-only and validates the Caddy configuration through
its healthcheck.

## Routing and rollback

- `/health`, `/info`, `/rendezvous/*`, `/webrtc/*` and `/signaling/*` remain on
  the public rendezvous service.
- Other paths, including `/signal`, reach the private room application.
- Upstream connection and response-header timeouts are bounded. An unavailable
  Mini-PC returns an error and never falls back to rendezvous.

Rollback consists of restoring the previous Caddy configuration and reloading
Caddy. Existing rendezvous records and TURN credentials are not modified by
the room edge.
