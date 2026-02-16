# Taiga Docker Compose Setup

Diese Konfiguration stellt eine selbst gehostete Taiga-Installation bereit
(Database, Redis, Backend, Frontend, Worker, Event-Service, Traefik).

## Verwendung
1. DNS-Eintraege setzen (`TAIGA_DOMAIN`, `KEYCLOAK_DOMAIN`).
2. Ports 80/443 oeffnen.
3. `.env.example` nach `.env` kopieren.
4. Stack starten:
```bash
docker compose --env-file .env up -d
```

## Sicherheit
- HTTPS-Weiterleitung via Traefik
- Security-Header und Rate-Limit aktiviert

## Betrieb
- Backups fuer DB/Media/Static/Event-Queue einplanen
- Vor Upgrades immer Backup erstellen