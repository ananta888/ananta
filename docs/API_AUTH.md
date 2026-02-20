# API-Authentifizierung & Autorisierung

Dieses Dokument beschreibt die Sicherheitsmechanismen der Ananta-API.

## Authentifizierungs-Methoden

Ananta unterstützt zwei primäre Wege der Authentifizierung:

### 1. Agent-Token (Statisch oder JWT)
Wird primär für die Kommunikation zwischen Agenten (Hub -> Worker) oder für administrative Aufgaben verwendet.

- **Header**: `Authorization: Bearer <AGENT_TOKEN>`
- **Query-Parameter**: `?token=<AGENT_TOKEN>` (Fallback)

Der `AGENT_TOKEN` wird über die Umgebungsvariable `AGENT_TOKEN` gesetzt. Falls der Token das Format eines JWT hat, wird er validiert. Andernfalls erfolgt ein direkter String-Vergleich.

**Wichtig:** Für die JWT-Validierung muss der `AGENT_TOKEN` mindestens **32 Bytes** lang sein. Kürzere Tokens werden nur als statischer String verglichen und können nicht für JWT-basierte Agent-Inter-Communication verwendet werden.

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

## Praktische Beispiele

### Login und Token-Erhalt

**curl:**
```bash
curl -X POST http://localhost:5000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'
```

**JavaScript (fetch):**
```javascript
const response = await fetch('http://localhost:5000/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'your_password' })
});
const data = await response.json();
const token = data.access_token;
```

### Authentifizierte API-Anfragen

**Mit User-JWT (curl):**
```bash
curl -X GET http://localhost:5000/tasks \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**Mit Agent-Token (curl):**
```bash
curl -X GET http://localhost:5000/agents \
  -H "Authorization: Bearer your_agent_token_here"
```

**JavaScript (fetch):**
```javascript
const response = await fetch('http://localhost:5000/tasks', {
  method: 'GET',
  headers: {
    'Authorization': `Bearer ${token}`
  }
});
const tasks = await response.json();
```

**Query-Parameter Fallback:**
```bash
curl -X GET "http://localhost:5000/tasks?token=your_token_here"
```

### Admin-Endpunkte aufrufen

```bash
curl -X POST http://localhost:5000/admin/settings \
  -H "Authorization: Bearer your_admin_token" \
  -H "Content-Type: application/json" \
  -d '{"key": "value"}'
```

## Troubleshooting

### Häufige Fehler und Lösungen

#### 401 Unauthorized - "Missing or invalid token"
**Ursache:** Kein Token im Authorization-Header oder Query-Parameter.

**Lösung:**
- Prüfen Sie, ob der `Authorization: Bearer <token>` Header korrekt gesetzt ist
- Alternativ: Token als Query-Parameter `?token=<token>` übergeben
- Bei User-Login: Stellen Sie sicher, dass `/login` erfolgreich war und Token gespeichert wurde

#### 401 Unauthorized - "Invalid token signature"
**Ursache:** Token wurde mit falschem Secret signiert oder ist beschädigt.

**Lösung:**
- Bei User-JWT: Prüfen Sie, ob `SECRET_KEY` in der Umgebung korrekt gesetzt ist
- Bei Agent-Token: Vergleichen Sie mit dem Wert der `AGENT_TOKEN` Umgebungsvariable
- Token könnte abgelaufen sein - fordern Sie einen neuen an

#### 403 Forbidden - "Admin rights required"
**Ursache:** Endpunkt erfordert Admin-Rechte, aber Token hat nur User-Rechte.

**Lösung:**
- Verwenden Sie den `AGENT_TOKEN` für volle Admin-Rechte
- Oder: Stellen Sie sicher, dass Ihr User-Account die Rolle `admin` hat
- Prüfen Sie mit: `curl -H "Authorization: Bearer <token>" http://localhost:5000/user/me`

#### Token läuft zu schnell ab
**Ursache:** Standard-TTL ist 3600 Sekunden (1 Stunde).

**Lösung:**
- Erhöhen Sie `AUTH_ACCESS_TOKEN_TTL_SECONDS` in der Umgebung
- Implementieren Sie Token-Refresh mit `/refresh-token` Endpunkt
- Für langlebige Automatisierung: Verwenden Sie `AGENT_TOKEN` statt User-JWT

#### Rate-Limit überschritten
**Ursache:** Zu viele fehlgeschlagene Login-Versuche.

**Lösung:**
- Warten Sie die Sperrzeit ab (Standard: 900 Sekunden)
- Prüfen Sie Logs auf wiederholte Fehlversuche
- Passen Sie `AUTH_RATE_LIMIT_MAX_ATTEMPTS_SHORT` an, falls nötig

#### CORS-Fehler im Browser
**Ursache:** Frontend und Backend auf unterschiedlichen Domains/Ports.

**Lösung:**
- Stellen Sie sicher, dass Flask-CORS korrekt konfiguriert ist
- Prüfen Sie `CORS_ORIGINS` Umgebungsvariable
- Bei Entwicklung: Verwenden Sie Proxy in `package.json` oder starten Sie Frontend/Backend auf gleichem Port

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
