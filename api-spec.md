# Ananta Agent API Spezifikation

Diese Dokumentation beschreibt die API-Endpunkte des Ananta Agenten.

## Basis-URL
`http://<agent-ip>:<port>` (Standard-Port: 5000)

## Authentifizierung
Die API verwendet Bearer-Tokens zur Authentifizierung. Eine detaillierte Übersicht über die Mechanismen und Middleware finden Sie in der [Authentifizierungs-Dokumentation](docs/api_auth_overview.md).
Der Token muss im `Authorization` Header gesendet werden:
`Authorization: Bearer <dein-token>`

---

## System Endpunkte

### Health Check
- **URL:** `/health`
- **Methode:** `GET`
- **Beschreibung:** Prüft den Status des Agenten.
- **Auth erforderlich:** Nein
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "agent": "name",
      "checks": {
        "shell": {"status": "ok"},
        "llm_providers": {"ollama": "ok"}
      }
    }
  }
  ```

### Readiness Check
- **URL:** `/ready`
- **Methode:** `GET`
- **Beschreibung:** Prüft, ob der Agent und seine Abhängigkeiten (Hub, LLM) bereit sind.
- **Auth erforderlich:** Nein
- **Rückgabe:** Detaillierter Status der Subsysteme im `data` Feld.
  ```json
  {
    "status": "success",
    "data": {
      "ready": true,
      "checks": {
        "hub": {"status": "ok", "latency": 0.05},
        "llm": {"provider": "ollama", "status": "ok", "latency": 0.1}
      }
    }
  }
  ```

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
- **Rückgabe:** `{"status": "success", "data": {"status": "registered"}}`

### Agenten auflisten
- **URL:** `/agents`
- **Methode:** `GET`
- **Beschreibung:** Listet alle bekannten Agenten auf.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Token rotieren
- **URL:** `/rotate-token`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "rotated", "new_token": "..."}}`

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
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "access_token": "...",
      "refresh_token": "...",
      "username": "...",
      "role": "..."
    }
  }
  ```

### Token erneuern
- **URL:** `/refresh-token`
- **Methode:** `POST`
- **Body:** `{"refresh_token": "string"}`
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "access_token": "...",
      "refresh_token": "...",
      "username": "...",
      "role": "..."
    }
  }
  ```

### Aktueller Benutzer (/me)
- **URL:** `/me`
- **Methode:** `GET`
- **Beschreibung:** Gibt Informationen über den aktuell angemeldeten Benutzer zurück.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "username": "...",
      "role": "...",
      "mfa_enabled": true
    }
  }
  ```

### Passwort ändern (Self-Service)
- **URL:** `/change-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (User)
- **Body:** `{"old_password": "...", "new_password": "..."}`
- **Rückgabe:** `{"status": "success", "data": {"status": "password_changed"}}`

### Benutzer auflisten (Admin)
- **URL:** `/users`
- **Methode:** `GET`
- **Auth erforderlich:** Ja (Admin)
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Benutzer anlegen (Admin)
- **URL:** `/users`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"username": "...", "password": "...", "role": "user|admin"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "user_created", "user": {...}}}`

### Benutzer löschen (Admin)
- **URL:** `/users/<username>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja (Admin)
- **Rückgabe:** `{"status": "success", "data": {"status": "user_deleted"}}`

### Passwort-Reset (Admin)
- **URL:** `/users/<username>/reset-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"new_password": "..."}`
- **Rückgabe:** `{"status": "success", "data": {"status": "password_reset"}}`

### Benutzer-Rolle aktualisieren (Admin)
- **URL:** `/users/<username>/role`
- **Methode:** `PUT`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"role": "user|admin"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "role_updated"}}`

---

## Multi-Faktor-Authentifizierung (MFA)

### MFA Setup initialisieren
- **URL:** `/mfa/setup`
- **Methode:** `POST`
- **Beschreibung:** Generiert ein neues MFA-Secret und einen QR-Code für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"status": "success", "data": {"secret": "...", "qr_code": "base64..."}}`

### MFA Verifizieren & Aktivieren
- **URL:** `/mfa/verify`
- **Methode:** `POST`
- **Beschreibung:** Verifiziert den ersten TOTP-Token und aktiviert MFA für den Account. Liefert 10 Backup-Codes zurück.
- **Rate-Limited:** Ja
- **Auth erforderlich:** Ja (User)
- **Body:** `{"token": "123456"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "mfa_enabled", "backup_codes": ["...", ...]}}`

### MFA Deaktivieren
- **URL:** `/mfa/disable`
- **Methode:** `POST`
- **Beschreibung:** Deaktiviert MFA für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"status": "success", "data": {"status": "mfa_disabled"}}`

---

## Konfiguration & Templates

### Konfiguration abrufen
- **URL:** `/config`
- **Methode:** `GET`
- **Beschreibung:** Gibt die aktuelle Agenten-Konfiguration zurück.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Konfiguration aktualisieren
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** JSON Objekt mit Konfigurationswerten.
- **Rückgabe:** `{"status": "success", "data": {"status": "updated", "config": {...}}}`

### Templates auflisten
- **URL:** `/templates`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Template erstellen
- **URL:** `/templates`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** Template Objekt.
- **Rückgabe:** `{"status": "success", "data": {...}}` (Erstelltes Template mit ID)

### Template aktualisieren
- **URL:** `/templates/<tpl_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Body:** Template Felder.
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Template löschen
- **URL:** `/templates/<tpl_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "deleted"}}`

---

## Task Management

### Global Terminal Logs
- **URL:** `/logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert die letzten 100 Einträge des globalen Terminal-Logs.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Schritt vorschlagen
- **URL:** `/step/propose`
- **Methode:** `POST`
- **Body:** `TaskStepProposeRequest`
- **Beschreibung:** Nutzt das LLM, um den nächsten Schritt basierend auf einem Prompt vorzuschlagen.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}` (TaskStepProposeResponse)

### Schritt ausführen
- **URL:** `/step/execute`
- **Methode:** `POST`
- **Body:** `TaskStepExecuteRequest`
- **Beschreibung:** Führt ein Shell-Kommando aus.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}` (TaskStepExecuteResponse)

### Tasks auflisten
- **URL:** `/tasks`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Task erstellen
- **URL:** `/tasks`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"id": "...", "status": "created"}}`

### Task Details
- **URL:** `/tasks/<tid>`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task aktualisieren
- **URL:** `/tasks/<tid>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"id": "...", "status": "updated"}}`

### Task zuweisen
- **URL:** `/tasks/<tid>/assign`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "assigned", "agent_url": "..."}}`

### Task Schritt vorschlagen
- **URL:** `/tasks/<tid>/step/propose`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task Schritt ausführen
- **URL:** `/tasks/<tid>/step/execute`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task Logs
- **URL:** `/tasks/<tid>/logs`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Task Logs Stream
- **URL:** `/tasks/<tid>/stream-logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert Logs als Server-Sent Events (SSE).
- **Auth erforderlich:** Ja

---

## System Statistiken & Events

### System Status (Echtzeit)
- **URL:** `/stats`
- **Methode:** `GET`
- **Beschreibung:** Liefert aktuelle Auslastung (CPU, RAM), Agenten-Status, Task-Zähler und Shell-Pool Status.
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "agents": {"total": 1, "online": 1, "offline": 0},
      "tasks": {"total": 5, "completed": 2, "failed": 0, "todo": 2, "in_progress": 1},
      "shell_pool": {"total": 5, "free": 4, "busy": 1},
      "resources": {"cpu_percent": 12.5, "ram_bytes": 102456789},
      "timestamp": 123456789.0,
      "agent_name": "hub-01"
    }
  }
  ```

### Statistik Historie
- **URL:** `/stats/history`
- **Methode:** `GET`
- **Beschreibung:** Liefert historische Snapshots der System-Statistiken.
- **Auth erforderlich:** Ja
- **Query Parameter:** `limit` (int), `offset` (int)
- **Rückgabe:** `{"status": "success", "data": [...]}`

### System Events (SSE)
- **URL:** `/events`
- **Methode:** `GET`
- **Beschreibung:** Streamt System-Events (z.B. Task-Updates, Agenten-Statusänderungen) als Server-Sent Events.
- **Auth erforderlich:** Ja

### Audit Logs (Admin)
- **URL:** `/audit-logs`
- **Methode:** `GET`
- **Beschreibung:** Ruft die Audit-Logs des Systems ab.
- **Auth erforderlich:** Ja (Admin)
- **Query Parameter:** `limit` (int), `offset` (int)
- **Rückgabe:** `{"status": "success", "data": [...]}`

---

## LLM

### CLI LLM ausfuehren (`/api/sgpt/execute`)
- **URL:** `/api/sgpt/execute`
- **Methode:** `POST`
- **Beschreibung:** Führt einen CLI-LLM-Befehl aus (SGPT, OpenCode, Aider oder Mistral Code).
- **Body:** `{"prompt": "...", "options": ["--shell", "--md", "--cache", "--no-cache", "--no-interaction"], "backend": "sgpt|opencode|aider|mistral_code|auto", "model": "optional-model-id"}`
- **Rückgabe:** `{"status": "success", "data": {"output": "...", "errors": "...", "backend": "sgpt|opencode|aider|mistral_code"}}`
- **Hinweis:** `options` werden backend-spezifisch validiert; nicht unterstützte Flags führen zu `400`.

### Verfügbare CLI Backends
- **URL:** `/api/sgpt/backends`
- **Methode:** `GET`
- **Beschreibung:** Liefert die unterstützten CLI-Backends, deren Capabilities und explizit nicht unterstützte Integrationen.
- **Rückgabe (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "configured_backend": "auto",
      "supported_backends": {
        "sgpt": {
          "display_name": "ShellGPT",
          "supports_model": true,
          "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"]
        },
        "opencode": {
          "display_name": "OpenCode",
          "supports_model": true,
          "supported_flags": []
        },
        "aider": {
          "display_name": "Aider",
          "supports_model": true,
          "supported_flags": []
        },
        "mistral_code": {
          "display_name": "Mistral Code",
          "supports_model": true,
          "supported_flags": []
        }
      }
    }
  }
  ```

### Text generieren (mit Tool-Calling und optionalem Streaming)
- **URL:** `/llm/generate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Tool-Ausführung erfordert Admin)
- **Beschreibung:** Führt eine LLM-Anfrage aus. Unterstützt optionales Tool-Calling (Allow-/Deny-List, Bestätigungspfad) und Streaming per Server-Sent Events (SSE).
- **Request Body:**
  ```json
  {
    "prompt": "string",                     // Freitext-Eingabe
    "tool_calls": [                          // Optional: vom Client vorgeschlagene Tool-Aufrufe
      {"name": "tool_name", "args": {}}
    ],
    "confirm_tool_calls": true,              // Optional: bestätigt, dass tool_calls direkt ausgeführt werden dürfen
    "confirmed": true,                       // Alias zu confirm_tool_calls
    "stream": false,                         // Falls true: Streaming-Antwort als SSE
    "history": [ {"role": "user|assistant|system", "content": "..."} ],
    "config": {
      "provider": "lmstudio|ollama|openai|anthropic",
      "model": "string|auto",
      "base_url": "string",
      "api_key": "string",
      "timeout": 30                         // Optional: per-Request Timeout in Sekunden; überschreibt globalen Default
    }
  }
  ```
  - Hinweis zu `config.timeout`: Gilt nur für diesen Request. Ohne Angabe wird der globale Wert `settings.http_timeout` verwendet (Standard 60 Sekunden).
- **Antworten (Non-Streaming):**
  - Erfolgreich ohne Tool-Calls:
    ```json
    {
      "status": "success",
      "data": {"response": "string"}
    }
    ```
  - JSON-Format mit vorgeschlagenen Tool-Calls (Benutzer ist kein Admin oder Bestätigung fehlt):
    ```json
    {
      "status": "success",
      "data": {
        "response": "Kurze Antwort",
        "requires_confirmation": true,
        "thought": "warum Aktion",
        "tool_calls": [ {"name": "tool", "args": {}} ]
      }
    }
    ```
  - Blockierte Tools (Deny-/Nicht erlaubt):
    ```json
    {
      "status": "success",
      "data": {
        "response": "Tool calls blocked: <liste>",
        "tool_results": [ {"tool":"name","success":false,"error":"tool_not_allowed"} ],
        "blocked_tools": ["name"]
      }
    }
    ```
  - Ausführung bestätigter Tool-Calls (Admin):
    ```json
    {
      "status": "success",
      "data": {
        "response": "string",
        "tool_results": [ {"tool":"name","success":true,"output":"..."} ]
      }
    }
    ```
- **Streaming:**
  - Bei `stream=true` wird als `text/event-stream` geantwortet. Nachrichten im Format `data: <chunk>\n\n`, abgeschlossen mit `event: done\ndata: [DONE]\n\n`.
  - Im Streaming-Modus erfolgt KEIN Tool-Calling; es wird Klartext gestreamt.

- **Status-/Fehlerfälle:**
  - `400 invalid_json | missing_prompt | llm_not_configured | llm_api_key_missing | llm_base_url_missing`
  - `403 forbidden` wenn Tool-Execution ohne Admin-Rechte angefragt wird.

### SGPT Hybrid-RAG Erweiterung

#### SGPT Execute mit Hybrid-Kontext
- **URL:** `/api/sgpt/execute`
- **Methode:** `POST`
- **Beschreibung:** Fuehrt SGPT/OpenCode/Aider/Mistral Code aus und kann optional Hybrid-RAG-Kontext einbetten.
- **Body:**
  ```json
  {
    "prompt": "Where is timeout handling implemented?",
    "options": ["--no-interaction"],
    "use_hybrid_context": true,
    "backend": "auto"
  }
  ```
- **Antwort (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "output": "....",
      "errors": "",
      "context": {
        "strategy": {"repository_map": 4, "semantic_search": 1, "agentic_search": 1},
        "policy_version": "v1",
        "chunk_count": 6,
        "token_estimate": 520
      }
    }
  }
  ```

#### SGPT Context Endpoint
- **URL:** `/api/sgpt/context`
- **Methode:** `POST`
- **Beschreibung:** Liefert den selektierten Kontextmix (Aider Symbol-Map, agentische Dateisuche, LlamaIndex Chunks).
- **Body:**
  ```json
  {
    "query": "find invoice timeout bug in module.py",
    "include_context_text": true
  }
  ```

#### SGPT Source Preview Endpoint
- **URL:** `/api/sgpt/source`
- **Methode:** `POST`
- **Beschreibung:** Gibt eine sichere Vorschau fuer eine zitierte Quelldatei zurueck.
- **Body:**
  ```json
  {
    "source_path": "agent/routes/sgpt.py",
    "max_chars": 1600
  }
  ```
- **Antwort (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "source_path": "agent/routes/sgpt.py",
      "preview": "def execute_sgpt():\n    ...",
      "truncated": true,
      "line_count": 42
    }
  }
  ```
## Team Management

### Teams auflisten
- **URL:** `/teams`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Team erstellen
- **URL:** `/teams`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** `TeamCreateRequest`
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Team aktualisieren
- **URL:** `/teams/<team_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Body:** `TeamUpdateRequest`
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Team löschen
- **URL:** `/teams/<team_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "deleted"}}`

### Team aktivieren
- **URL:** `/teams/<team_id>/activate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "activated"}}`

### Scrum Team Setup (Shortcut)
- **URL:** `/teams/setup-scrum`
- **Methode:** `POST`
- **Beschreibung:** Erstellt ein standardisiertes Scrum-Team mit vordefinierten Rollen (Product Owner, Scrum Master, Developer).
- **Body:** `{"name": "Team Name"}`
- **Rückgabe:** `{"status": "success", "message": "...", "data": {"team": {...}}}`

### Team-Typen auflisten
- **URL:** `/teams/types`
- **Methode:** `GET`
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Rollen auflisten
- **URL:** `/teams/roles`
- **Methode:** `GET`
- **Rückgabe:** `{"status": "success", "data": [...]}`

---

## Auto-Planner (Goal-basierte Task-Generierung)

Der Auto-Planner analysiert High-Level-Ziele und generiert automatisch strukturierte Subtasks.

### Auto-Planner Status
- **URL:** `/tasks/auto-planner/status`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "enabled": false,
      "auto_followup_enabled": true,
      "max_subtasks_per_goal": 10,
      "default_priority": "Medium",
      "auto_start_autopilot": true,
      "llm_timeout": 30,
      "stats": {
        "goals_processed": 0,
        "tasks_created": 0,
        "followups_created": 0,
        "errors": 0
      }
    }
  }
  ```

### Auto-Planner konfigurieren
- **URL:** `/tasks/auto-planner/configure`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:**
  ```json
  {
    "enabled": true,
    "auto_followup_enabled": true,
    "max_subtasks_per_goal": 10,
    "default_priority": "Medium",
    "auto_start_autopilot": true,
    "llm_timeout": 30
  }
  ```
- **Rückgabe:** Aktualisierte Konfiguration

### Goal planen und Tasks erstellen
- **URL:** `/tasks/auto-planner/plan`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Analysiert ein Goal mit dem LLM und erstellt automatisch Subtasks.
- **Body:**
  ```json
  {
    "goal": "Implementiere ein User-Login-System mit JWT-Authentifizierung",
    "context": "Verwende Flask und PostgreSQL",
    "team_id": "optional-team-id",
    "parent_task_id": "optional-parent-id",
    "create_tasks": true
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "subtasks": [
        {"title": "...", "description": "...", "priority": "High"}
      ],
      "created_task_ids": ["goal-abc123", "goal-def456"],
      "raw_response": null
    }
  }
  ```

### Task auf Folgeaufgaben analysieren
- **URL:** `/tasks/auto-planner/analyze/<task_id>`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Analysiert einen abgeschlossenen Task auf natürliche Folgeaufgaben.
- **Body:** (optional)
  ```json
  {
    "output": "Überschreibt die Task-Ausgabe",
    "exit_code": 0
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "followups_created": [
        {"id": "followup-123", "title": "Tests schreiben", "priority": "Medium"}
      ],
      "analysis": {
        "task_complete": true,
        "needs_review": false,
        "suggestions": ["Dokumentation ergänzen"]
      }
    }
  }
  ```

---

## Trigger-System (Webhooks)

Das Trigger-System ermöglicht die automatische Task-Erstellung aus externen Quellen.

### Trigger-Status
- **URL:** `/triggers/status`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "enabled_sources": ["generic", "github"],
      "configured_handlers": ["generic", "github"],
      "webhook_secrets_configured": ["github"],
      "ip_whitelists": {
        "github": ["192.168.1.0/24", "10.0.0.1"],
        "slack": ["10.0.0.5"]
      },
      "rate_limits": {
        "github": {"max_requests": 60, "window_seconds": 60},
        "slack": {"max_requests": 30, "window_seconds": 60}
      },
      "stats": {
        "webhooks_received": 10,
        "tasks_created": 8,
        "rejected": 2,
        "rate_limited": 1,
        "ip_blocked": 0
      },
      "auto_start_planner": true
    }
  }
  ```

### Trigger konfigurieren
- **URL:** `/triggers/configure`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:**
  ```json
  {
    "enabled_sources": ["generic", "github", "slack"],
    "webhook_secrets": {
      "github": "your-webhook-secret",
      "slack": "another-secret"
    },
    "ip_whitelists": {
      "github": ["192.168.1.0/24", "10.0.0.1"],
      "slack": ["10.0.0.5"]
    },
    "rate_limits": {
      "github": {"max_requests": 60, "window_seconds": 60},
      "slack": {"max_requests": 30, "window_seconds": 60}
    },
    "auto_start_planner": true
  }
  ```
- **Sicherheitsparameter:**
  - `ip_whitelists` (optional): Dict mit Source -> Liste erlaubter IPs. Leere Liste = alle IPs erlaubt.
  - `rate_limits` (optional): Dict mit Source -> `{max_requests, window_seconds}`. Default: 60 Requests / 60 Sekunden.
- **Rückgabe:** Aktualisierte Konfiguration

### Webhook empfangen
- **URL:** `/triggers/webhook/<source>`
- **Methode:** `POST`
- **Auth erforderlich:** Nein (Signatur-Validierung optional)
- **Beschreibung:** Empfängt Webhooks von externen Quellen.
- **Header:**
  - `X-Hub-Signature-256`: HMAC-SHA256 Signatur (falls Secret konfiguriert)
- **Body (Beispiel generic):**
  ```json
  {
    "title": "Bug Report: Login fehlschlägt",
    "description": "Der Login-Button reagiert nicht...",
    "priority": "High",
    "tags": ["bug", "auth"]
  }
  ```
- **Body (Beispiel mehrere Tasks):**
  ```json
  {
    "tasks": [
      {"title": "Task 1", "description": "...", "priority": "High"},
      {"title": "Task 2", "description": "...", "priority": "Medium"}
    ]
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "status": "processed",
      "tasks_created": 2,
      "task_ids": ["trg-gene-abc123", "trg-gene-def456"]
    }
  }
  ```
- **Fehler-Codes:**
  - `401 invalid_signature` - Webhook-Signatur ungültig
  - `403 source_disabled` - Quelle ist deaktiviert
  - `403 ip_not_whitelisted` - Client-IP nicht in Whitelist
  - `429 rate_limit_exceeded` - Zu viele Requests von dieser IP

### Trigger testen
- **URL:** `/triggers/test`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Testet einen Trigger ohne Tasks zu erstellen.
- **Body:**
  ```json
  {
    "source": "generic",
    "payload": {"title": "Test Task", "description": "Nur ein Test"}
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "source": "generic",
      "parsed_tasks": [{"title": "Test Task", "description": "Nur ein Test"}],
      "would_create": 1
    }
  }
  ```

### Unterstützte Webhook-Quellen

| Source | Beschreibung | Payload-Format |
|--------|--------------|----------------|
| `generic` | Allgemeine JSON-Webhooks | `{title, description, priority, tasks[]}` |
| `github` | GitHub Issues & PRs | GitHub Webhook Format |
| `slack` | Slack Events | Slack Event API Format |
| `jira` | Jira Issue Events | Jira Webhook Format |

---


