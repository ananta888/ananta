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
- **Beschreibung:** Prüft, ob der Agent und seine Abhängigkeiten (Controller, LLM) bereit sind.
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
    "role": "worker|controller",
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
- **URL:** `/tasks/<tid>/propose`
- **Methode:** `POST`
- **Auth erforderlich:** Ja

### Task Schritt ausführen
- **URL:** `/tasks/<tid>/execute`
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
