# API Authentifizierungs-Übersicht

Diese Dokumentation beschreibt die Authentifizierungsmechanismen des Ananta Agenten, die in der Datei `agent/auth.py` implementiert sind.

## Authentifizierungsmethoden

Der Agent unterstützt zwei Hauptarten der Authentifizierung:

1.  **Agent-Token (System-Authentifizierung):**
    *   Wird für die Kommunikation zwischen Agenten oder für administrative Aufgaben verwendet.
    *   Kann ein statischer String sein (konfiguriert via `AGENT_TOKEN`).
    *   Kann ein JWT sein, der mit dem `AGENT_TOKEN` signiert wurde.
    *   Gewährt in der Regel volle Admin-Rechte (`g.is_admin = True`).

2.  **Benutzer-JWT (User-Authentifizierung):**
    *   Wird für Endbenutzer-Zugriffe (z.B. über das Frontend) verwendet.
    *   Wird nach einem erfolgreichen Login (`/login`) ausgestellt.
    *   Wird mit dem globalen `SECRET_KEY` (aus den Settings) signiert.
    *   Enthält Benutzerrollen (z.B. `admin` oder `user`).

## Nutzung im Authorization Header

Beide Methoden verwenden den `Authorization` Header im Bearer-Format:

```http
Authorization: Bearer <token>
```

Alternativ kann der Token bei einigen Endpunkten als Query-Parameter übergeben werden: `?token=<token>`.

## Middleware & Decorators

In `agent/auth.py` sind verschiedene Decorators definiert, um Endpunkte zu schützen:

### 1. `@check_auth`
Dies ist der flexibelste Decorator. Er prüft nacheinander:
- Ob der Token dem statischen `AGENT_TOKEN` entspricht.
- Ob der Token ein JWT ist, der mit `AGENT_TOKEN` signiert wurde.
- Ob der Token ein Benutzer-JWT ist (signiert mit `SECRET_KEY`).

Wenn ein valider Agent-Token gefunden wird, wird `g.is_admin` auf `True` gesetzt. Bei einem Benutzer-JWT werden die Benutzerdaten in `g.user` geladen und `g.is_admin` basierend auf der Rolle im JWT gesetzt.

### 2. `@check_user_auth`
Erfordert zwingend einen Benutzer-JWT, der mit dem `SECRET_KEY` signiert wurde. Agent-Token werden hier abgelehnt.

### 3. `@admin_required`
Stellt sicher, dass der Anreifer Admin-Rechte besitzt. Dies ist der Fall, wenn:
- Ein valider `AGENT_TOKEN` verwendet wurde.
- Ein Benutzer-JWT mit der Rolle `admin` verwendet wurde.

## Token-Rotation

Über den Endpunkt `/rotate-token` kann der `AGENT_TOKEN` erneuert werden. 
1. Ein neues Secret wird generiert.
2. Der neue Token wird (falls konfiguriert) an den Hub gemeldet.
3. Bei Erfolg wird der Token lokal gespeichert und in der laufenden Applikation aktualisiert.

## Sicherheitshinweise

*   Wenn kein `AGENT_TOKEN` gesetzt ist, loggt der Agent eine Warnung und lässt Anfragen ohne Authentifizierung zu (nur für lokale Entwicklung empfohlen).
*   Benutzer-Tokens haben eine begrenzte Gültigkeit und sollten über den `/refresh-token` Endpunkt erneuert werden.
