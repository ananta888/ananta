# Ananta Agent API Spezifikation

Diese Dokumentation beschreibt die API-Endpunkte des Ananta Agenten.

## Basis-URL
`http://<agent-ip>:<port>` (Standard-Port: 5000)

## Authentifizierung
Die API verwendet Bearer-Tokens zur Authentifizierung.
Der Token muss im `Authorization` Header gesendet werden:
`Authorization: Bearer <dein-token>`

---

## System Endpunkte

### Health Check
- **URL:** `/health`
- **Methode:** `GET`
- **Beschreibung:** Prüft den Status des Agenten.
- **Auth erforderlich:** Nein
- **Rückgabe:** `{"status": "ok", "agent": "name"}`

### Readiness Check
- **URL:** `/ready`
- **Methode:** `GET`
- **Beschreibung:** Prüft, ob der Agent und seine Abhängigkeiten (Hub, LLM) bereit sind.
- **Auth erforderlich:** Nein
- **Rückgabe:** Detaillierter Status der Subsysteme.

### Metriken
- **URL:** `/metrics`
- **Methode:** `GET`
- **Beschreibung:** Liefert Prometheus-Metriken.
- **Auth erforderlich:** Nein
- **Rückgabe:** Plain text (Prometheus Format)

### Agent Registrierung
- **URL:** `/register`
- **Methode:** `POST`
- **Beschreibung:** Registriert einen neuen Agenten.
- **Auth erforderlich:** Nein (Rate-limited)
- **Body:**
  ```json
  {
    "name": "string",
    "url": "string",
    "role": "worker|hub",
    "token": "optional string"
  }
  ```
- **Rückgabe:** `{"status": "registered"}`

### Agenten auflisten
- **URL:** `/agents`
- **Methode:** `GET`
- **Beschreibung:** Listet alle bekannten Agenten auf.
- **Auth erforderlich:** Ja

### Token rotieren
- **URL:** `/rotate-token`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "rotated", "new_token": "..."}`

---

## Authentifizierung & Benutzerverwaltung

### Login
- **URL:** `/login`
- **Methode:** `POST`
- **Beschreibung:** Meldet einen Benutzer an und liefert ein JWT.
- **Rate-Limited:** Ja (max 5 Versuche / Minute)
- **Body:**
  ```json
  {
    "username": "string",
    "password": "string"
  }
  ```
- **Rückgabe:** Token und Refresh-Token.

### Token erneuern
- **URL:** `/refresh-token`
- **Methode:** `POST`
- **Body:** `{"refresh_token": "string"}`

### Aktueller Benutzer (/me)
- **URL:** `/me`
- **Methode:** `GET`
- **Beschreibung:** Gibt Informationen über den aktuell angemeldeten Benutzer zurück.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"username": "...", "role": "...", "mfa_enabled": bool}`

### Passwort ändern (Self-Service)
- **URL:** `/change-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (User)
- **Body:** `{"old_password": "...", "new_password": "..."}`

### Benutzer auflisten (Admin)
- **URL:** `/users`
- **Methode:** `GET`
- **Auth erforderlich:** Ja (Admin)

### Benutzer anlegen (Admin)
- **URL:** `/users`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"username": "...", "password": "...", "role": "user|admin"}`

### Benutzer löschen (Admin)
- **URL:** `/users/<username>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja (Admin)

### Passwort-Reset (Admin)
- **URL:** `/users/<username>/reset-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"new_password": "..."}`

### Benutzer-Rolle aktualisieren (Admin)
- **URL:** `/users/<username>/role`
- **Methode:** `PUT`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"role": "user|admin"}`

---

## Multi-Faktor-Authentifizierung (MFA)

### MFA Setup initialisieren
- **URL:** `/mfa/setup`
- **Methode:** `POST`
- **Beschreibung:** Generiert ein neues MFA-Secret und einen QR-Code für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"secret": "...", "qr_code": "base64..."}`

### MFA Verifizieren & Aktivieren
- **URL:** `/mfa/verify`
- **Methode:** `POST`
- **Beschreibung:** Verifiziert den ersten TOTP-Token und aktiviert MFA für den Account.
- **Rate-Limited:** Ja
- **Auth erforderlich:** Ja (User)
- **Body:** `{"token": "123456"}`
- **Rückgabe:** `{"status": "mfa_enabled"}`

### MFA Deaktivieren
- **URL:** `/mfa/disable`
- **Methode:** `POST`
- **Beschreibung:** Deaktiviert MFA für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"status": "mfa_disabled"}`

---

## Konfiguration & Templates

### Konfiguration abrufen
- **URL:** `/config`
- **Methode:** `GET`
- **Beschreibung:** Gibt die aktuelle Agenten-Konfiguration zurück.
- **Auth erforderlich:** Ja

### Konfiguration aktualisieren
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** JSON Objekt mit Konfigurationswerten.
- **Rückgabe:** `{"status": "updated", "config": {...}}`

### Templates auflisten
- **URL:** `/templates`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** Liste von Templates.

### Template erstellen
- **URL:** `/templates`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** Template Objekt.
- **Rückgabe:** Erstelltes Template mit ID.

### Template aktualisieren
- **URL:** `/templates/<tpl_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Body:** Template Felder.

### Template löschen
- **URL:** `/templates/<tpl_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja

---

## Task Management

### Global Terminal Logs
- **URL:** `/logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert die letzten 100 Einträge des globalen Terminal-Logs.
- **Auth erforderlich:** Ja

### Schritt vorschlagen
- **URL:** `/step/propose`
- **Methode:** `POST`
- **Body:** `TaskStepProposeRequest`
- **Beschreibung:** Nutzt das LLM, um den nächsten Schritt basierend auf einem Prompt vorzuschlagen.
- **Auth erforderlich:** Ja

### Schritt ausführen
- **URL:** `/step/execute`
- **Methode:** `POST`
- **Body:** `TaskStepExecuteRequest`
- **Beschreibung:** Führt ein Shell-Kommando aus.
- **Auth erforderlich:** Ja

### Tasks auflisten
- **URL:** `/tasks`
- **Methode:** `GET`
- **Auth erforderlich:** Ja

### Task erstellen
- **URL:** `/tasks`
- **Methode:** `POST`
- **Auth erforderlich:** Ja

### Task Details
- **URL:** `/tasks/<tid>`
- **Methode:** `GET`
- **Auth erforderlich:** Ja

### Task aktualisieren
- **URL:** `/tasks/<tid>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja

### Task zuweisen
- **URL:** `/tasks/<tid>/assign`
- **Methode:** `POST`
- **Auth erforderlich:** Ja

### Task Schritt vorschlagen
- **URL:** `/tasks/<tid>/step/propose`
- **Methode:** `POST`
- **Auth erforderlich:** Ja

### Task Schritt ausführen
- **URL:** `/tasks/<tid>/step/execute`
- **Methode:** `POST`
- **Auth erforderlich:** Ja

### Task Logs
- **URL:** `/tasks/<tid>/logs`
- **Methode:** `GET`
- **Auth erforderlich:** Ja

### Task Logs Stream
- **URL:** `/tasks/<tid>/stream-logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert Logs als Server-Sent Events (SSE).
- **Auth erforderlich:** Ja
