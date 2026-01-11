# Ananta Agent API Spezifikation

Diese Dokumentation beschreibt die API-Endpunkte des Ananta Agenten.

## Basis-URL
`http://<agent-ip>:<port>` (Standard-Port: 5000)

## Authentifizierung
Einige Endpunkte erfordern einen `X-Agent-Token` Header.

---

## System Endpunkte

### Health Check
- **URL:** `/health`
- **Methode:** `GET`
- **Beschreibung:** Prüft den Status des Agenten.
- **Rückgabe:** `{"status": "ok", "agent": "name"}`

### Metriken
- **URL:** `/metrics`
- **Methode:** `GET`
- **Beschreibung:** Liefert Prometheus-Metriken.
- **Rückgabe:** Plain text (Prometheus Format)

### Agent Registrierung
- **URL:** `/register`
- **Methode:** `POST`
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

### Konfiguration aktualisieren
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** JSON Objekt mit Konfigurationswerten.
- **Rückgabe:** `{"status": "updated", "config": {...}}`

### Templates auflisten
- **URL:** `/templates`
- **Methode:** `GET`
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

### Schritt vorschlagen (Legacy)
- **URL:** `/propose`
- **Methode:** `POST`
- **Body:** `TaskStepProposeRequest`
- **Beschreibung:** Nutzt das LLM, um den nächsten Schritt basierend auf einem Prompt vorzuschlagen.

### Schritt ausführen (Legacy)
- **URL:** `/execute`
- **Methode:** `POST`
- **Body:** `TaskStepExecuteRequest`
- **Beschreibung:** Führt ein Shell-Kommando aus.

### Tasks auflisten
- **URL:** `/tasks`
- **Methode:** `GET`

### Task erstellen
- **URL:** `/tasks`
- **Methode:** `POST`

### Task Details
- **URL:** `/tasks/<tid>`
- **Methode:** `GET`

### Task aktualisieren
- **URL:** `/tasks/<tid>`
- **Methode:** `PATCH`

### Task zuweisen
- **URL:** `/tasks/<tid>/assign`
- **Methode:** `POST`

### Task Schritt vorschlagen
- **URL:** `/tasks/<tid>/propose`
- **Methode:** `POST`

### Task Schritt ausführen
- **URL:** `/tasks/<tid>/execute`
- **Methode:** `POST`

### Task Logs
- **URL:** `/tasks/<tid>/logs`
- **Methode:** `GET`

### Task Logs Stream
- **URL:** `/tasks/<tid>/logs/stream`
- **Methode:** `GET`
- **Beschreibung:** Liefert Logs als Server-Sent Events (SSE).
