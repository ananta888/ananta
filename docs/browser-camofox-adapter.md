# Camofox Browser Backend

Camofox (basierend auf [Camoufox](https://github.com/daijro/camoufox)) ist ein Firefox-basiertes Browser-Backend mit Anti-Bot-Schutz. Es wird in Ananta als optionales, explizit konfigurierbares Backend hinter dem bestehenden Browser-Policy-Stack integriert.

## Architektur

```
Agent/Worker
    └─> BrowserIntentMapper
            └─> BrowserTaskContract  (Constraints festlegen)
                    └─> BrowserPolicyService  (SECURITY GATE)
                            └─> BrowserCamofoxAdapter
                                    └─> Camofox REST-Server (:9377)
                                            └─> Firefox-Session
```

**Sicherheitsgrenzen:** `BrowserPolicyService` liegt immer vor `BrowserCamofoxAdapter`. Der Adapter ist niemals direkt aus einem Agent-Prompt erreichbar.

## Config

```json
{
  "research_backend": {
    "provider": "camofox",
    "providers": {
      "camofox": {
        "enabled": true,
        "mode": "native",
        "camofox_url": "http://localhost:9377",
        "timeout_seconds": 30,
        "allowed_domains": ["example.com"],
        "blocked_domains": ["localhost", "127.0.0.1", "169.254.169.254"],
        "download_policy": "deny",
        "auth_policy": "none",
        "screenshot_policy": "on_error",
        "persist_session": false,
        "max_actions": 10
      }
    }
  }
}
```

**Default:** Camofox ist nicht aktiviert (`enabled: false`). Bestehendes Verhalten bleibt unverändert.

## Sicherheitsmodell

### Default-Deny

| Aktion | Default |
|--------|---------|
| Navigation | Nur zu explizit erlaubten Domains (`allowed_domains`) |
| Downloads | `deny` — muss explizit auf `whitelist` oder `bounded_output_dir` gesetzt werden |
| Auth | `none` — opt-in via `auth_policy: explicit_opt_in` |
| Screenshot | `none` — opt-in via `screenshot_policy: on_error` oder `always` |
| Session-Persistenz | `false` — Sessions werden immer geschlossen |

### Immer blockierte Hosts

Unabhängig von `allowed_domains` werden immer blockiert:
- `localhost`, `127.0.0.x` (Loopback)
- `169.254.x.x` (AWS/GCP Metadata IP)
- `metadata.google.internal`
- RFC1918 Private Ranges: `10.x.x.x`, `172.16–31.x.x`, `192.168.x.x`
- IPv6: `::1` (Loopback), `fc00::/7` (ULA)

### Audit-Logging

Alle sicherheitsrelevanten Aktionen erzeugen Audit-Events:
- `browser_camofox_navigate` — erfolgreiche Navigation
- `browser_camofox_download` — Datei-Download
- `browser_camofox_policy_denied` — Policy-Verstoß (Domain, IP, Download, Auth)
- `browser_camofox_session_close` — Session-Ende
- `browser_camofox_health_fail` — Server nicht erreichbar

Audit-Logs enthalten **keine** Passwörter, Cookies, vollständige Formulardaten oder Session-Token.

## Lokaler Start

### Direkt (Python)

```bash
pip install camoufox
camoufox fetch
python -m camoufox server --port 9377
```

### Docker (optional)

```yaml
# docker-compose.yml (Ergänzung)
services:
  camofox:
    image: ghcr.io/daijro/camoufox:latest
    ports:
      - "9377:9377"
    restart: unless-stopped
```

```bash
docker compose up camofox
```

## Testen

### Unit-Tests (kein Server nötig)

```bash
pytest tests/test_browser_camofox_adapter.py -v
```

### Policy-Tests

```bash
pytest tests/test_browser_policy_service.py tests/test_browser_task_contract.py -v
```

### Integrationstests (Server nötig)

```bash
CAMOFOX_TEST_URL=http://localhost:9377 pytest tests/test_browser_camofox_integration.py -v
```

## Adapter-API

```python
from agent.services.browser_camofox_adapter import build_camofox_adapter

adapter = build_camofox_adapter({"camofox_url": "http://localhost:9377"})

# Health-Check
health = adapter.health_check()

# Session erstellen
session_id = adapter.create_session(contract=contract)

# Navigation (Policy-geprüft)
result = adapter.navigate(url="https://example.com", session_id=session_id, contract=contract)

# Seiteninhalt lesen
page = adapter.read_page(session_id=session_id, contract=contract)

# Session schließen (immer!)
adapter.close_session(session_id=session_id)
```

## MCP-Anbindung (zukünftige Ausbaustufe)

MCP ist **nicht** die erste Integrationsstufe. REST-Adapter läuft zuerst stabil, dann kann MCP als zusätzlicher Tool-Transport ergänzt werden — aber nur hinter demselben Policy-Gate. Keine direkte MCP-Freischaltung ohne `BrowserPolicyService`.

## Bekannte Grenzen

- Camofox-Server muss separat gestartet werden (kein Auto-Start durch Ananta)
- Session-Persistenz (`persist_session: true`) ist möglich, aber standardmäßig deaktiviert
- Kein paralleles Browsing über mehrere Sessions in einer Anfrage
- Browser-Profile, Cookies und Local-Storage werden nicht an den LLM-Kontext weitergegeben
- Downloads landen nur im konfigurierten `output_dir` — kein Zugriff auf beliebige Pfade
