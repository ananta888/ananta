# LAN HTTPS for the local Compose stack

The development containers keep their internal HTTP endpoints. The optional
LAN overlay terminates TLS in a dedicated Caddy container and exposes:

- `https://<ANANTA_LAN_HOST>:4200` for Angular
- `https://<ANANTA_LAN_HOST>:5000` for the Hub API

This preserves the Hub as the control plane and does not expose worker ports.

Configure `.env` with the LAN address and the exact browser origin:

```dotenv
ANANTA_LAN_HOST=192.168.178.103
CORS_ORIGINS=http://localhost:4200,http://127.0.0.1:4200,https://192.168.178.103:4200
```

Start the Ollama development stack with the additive TLS overlay:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.dev.ollama.yml \
  -f docker/compose-next/compose.lan-https.yml \
  up -d
```

The proxy listens on WSL ports `8443` and `5443` by default. Under WSL2,
forward Windows LAN ports `4200` and `5000` to those two target ports. The
existing client URLs therefore remain unchanged.

Caddy uses its local certificate authority. Copy
`/data/caddy/pki/authorities/local/root.crt` from the `lan-tls-proxy`
container and trust only that public root certificate on each client device.
Never export or distribute the CA private key from the Caddy data volume.
