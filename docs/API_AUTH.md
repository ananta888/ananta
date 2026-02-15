# API-Authentifizierung & Autorisierung

Dieses Dokument beschreibt die Sicherheitsmechanismen der Ananta-API.

## Authentifizierungs-Methoden

Ananta unterstützt zwei primäre Wege der Authentifizierung:

### 1. Agent-Token (Statisch oder JWT)
Wird primär für die Kommunikation zwischen Agenten (Hub -> Worker) oder für administrative Aufgaben verwendet.

- **Header**: `Authorization: Bearer <AGENT_TOKEN>`
- **Query-Parameter**: `?token=<AGENT_TOKEN>` (Fallback)

Der `AGENT_TOKEN` wird über die Umgebungsvariable `AGENT_TOKEN` gesetzt. Falls der Token das Format eines JWT hat, wird er validiert. Andernfalls erfolgt ein direkter String-Vergleich.

Besitzer des `AGENT_TOKEN` haben automatisch **Admin-Rechte**.

### 2. Benutzer-JWT (Dynamisch)
Wird für Endbenutzer des Dashboards verwendet.

- **Header**: `Authorization: Bearer <JWT_TOKEN>`

Die Tokens werden nach einem erfolgreichen Login am `/login`-Endpunkt ausgegeben und sind zeitlich begrenzt. Sie werden mit dem systemweiten `SECRET_KEY` signiert.

## Rollen & Berechtigungen

Es gibt zwei Rollenstufen:

| Rolle | Berechtigung |
| :--- | :--- |
| **admin** | Voller Zugriff auf alle Endpunkte (Löschen, Konfiguration, User-Management). |
| **user** | Eingeschränkter Zugriff (Tasks ansehen, eigene Tasks bearbeiten, keine System-Config). |

## Implementierung im Code

### Decorators

In `agent/auth.py` sind folgende Decorators definiert:

- `@check_auth`: Prüft, ob überhaupt ein gültiger Token (Agent oder User) vorhanden ist.
- `@check_user_auth`: Erfordert zwingend ein gültiges Benutzer-JWT.
- `@admin_required`: Erfordert Admin-Rechte (entweder über den `AGENT_TOKEN` oder ein User-JWT mit der Rolle `admin`).

### Beispiel-Nutzung (Flask-Route)

Integrierte Flask-Decorators ermöglichen eine einfache Absicherung der Endpunkte:

```python
from agent.auth import check_auth, admin_required, check_user_auth

@app.route("/public")
def public():
    return "Public content"

@app.route("/secure")
@check_auth
def secure():
    # Gültiger Token erforderlich (Agent oder User)
    return "Authorized"

@app.route("/user-only")
@check_user_auth
def user_only():
    # Nur User-JWTs erlaubt
    return "User Authorized"

@app.route("/admin")
@admin_required
def admin():
    # Admin-Rechte erforderlich
    return "Admin Area"
```

## Middleware-Konzept

Die Authentifizierung findet vor der eigentlichen Route-Verarbeitung statt. 
1. **Header-Extraktion**: Der Token wird aus `Authorization: Bearer <token>` oder dem Query-Parameter `?token=...` extrahiert.
2. **Validierung**:
   - Statischer Vergleich gegen `AGENT_TOKEN`.
   - JWT-Dekodierung mit `AGENT_TOKEN` als Secret (für Agent-Inter-Communication).
   - JWT-Dekodierung mit systemweitem `SECRET_KEY` (für User-Logins).
3. **Kontext-Zuweisung**: Bei Erfolg werden `g.user` (Payload) und `g.is_admin` (Boolean) im Flask-Global-Objekt hinterlegt.

## Token-Rotation

Admins können den `AGENT_TOKEN` im laufenden Betrieb rotieren. Dabei wird der neue Token generiert, lokal persistiert und automatisch an den Hub gemeldet, um die Erreichbarkeit sicherzustellen.

## Auth Security ENV-Parameter

Folgende optionale Umgebungsvariablen steuern Rate-Limits, Token-TTLs und Lockout-Verhalten:

- AUTH_RATE_LIMIT_WINDOW_SHORT_SECONDS (Default: 60)
- AUTH_RATE_LIMIT_MAX_ATTEMPTS_SHORT (Default: 10)
- AUTH_RATE_LIMIT_WINDOW_LONG_SECONDS (Default: 3600)
- AUTH_RATE_LIMIT_MAX_ATTEMPTS_LONG (Default: 50)
- AUTH_IP_BAN_DURATION_SECONDS (Default: 86400)
- AUTH_ACCESS_TOKEN_TTL_SECONDS (Default: 3600)
- AUTH_REFRESH_TOKEN_TTL_SECONDS (Default: 604800)
- AUTH_USER_LOCKOUT_THRESHOLD (Default: 5)
- AUTH_USER_LOCKOUT_DURATION_SECONDS (Default: 900)
- AUTH_PASSWORD_MIN_LENGTH (Default: 12)
- AUTH_PASSWORD_HISTORY_LIMIT (Default: 3)
- AUTH_MFA_BACKUP_CODE_COUNT (Default: 10)
