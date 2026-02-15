# Backend Dokumentation

Dieses Dokument bietet einen detaillierten Einblick in die Backend-Architektur, die Datenmodelle und das Authentifizierungssystem von Ananta.

---

## 🏗️ Architekturübersicht

Ananta Backend basiert auf **FastAPI** (oder Flask, je nach Modul) und nutzt **SQLModel** für die Datenbank-Interaktion. Es folgt dem Repository-Pattern, um Geschäftslogik von der Datenhaltung zu trennen.

### Schichten:
1.  **API Layer (`ai_agent.py`)**: Endpunkte, Request-Validierung und Routing.
2.  **Service Layer**: Orchestrierung der Logik (Task-Management, LLM-Integration).
3.  **Repository Layer (`repository.py`)**: Abstraktion des Datenbankzugriffs.
4.  **Model Layer (`db_models.py`)**: Definition der SQL-Tabellen via SQLModel.

---

## 📊 Datenmodelle (ORM)

Ananta nutzt SQLModel für ein konsistentes Schema. Hier sind die wichtigsten Tabellen und ihre Bedeutung:

### Benutzer & Sicherheit
-   **`UserDB`**: Speichert Benutzernamen, Passwort-Hashes, Rollen (`admin`/`user`) und MFA-Konfiguration (Secret, Enabled-Flag, Backup-Codes).
-   **`RefreshTokenDB`**: Speichert gültige Refresh-Tokens für die JWT-Erneuerung.
-   **`PasswordHistoryDB`**: Verhindert die Wiederverwendung alter Passwörter.
-   **`LoginAttemptDB` & `BannedIPDB`**: Schutz gegen Brute-Force-Angriffe.

### Agenten & Teams
-   **`AgentInfoDB`**: Liste aller registrierten Agenten (Hub/Worker) mit URL, Rolle und Status.
-   **`TeamDB`**: Gruppierung von Agenten zu funktionalen Teams.
-   **`TeamTypeDB`**: Vorlagen für Team-Strukturen (z.B. "Dev-Team", "QA-Team").
-   **`RoleDB`**: Definition von Rollen innerhalb eines Teams (z.B. "Architect", "Coder").
-   **`TeamMemberDB`**: Verknüpfung von Agenten, Teams und Rollen.

### Tasks & Templates
-   **`TaskDB`**: Das Herzstück. Speichert Titel, Beschreibung, Status (`todo`, `in-progress`, `done`), Priorität und die gesamte Historie der LLM-Vorschläge und Ausführungen.
-   **`TemplateDB`**: Wiederverwendbare Prompt-Templates.
-   **`ArchivedTaskDB`**: Kopie von abgeschlossenen Tasks für die Langzeit-Historie.

---

## 🔐 Authentifizierung & Autorisierung

### 1. API-Authentifizierung
Ananta nutzt zwei primäre Mechanismen:

-   **JWT (JSON Web Token)**:
    -   Erhalten über `/login`.
    -   Muss im `Authorization: Bearer <token>` Header gesendet werden.
    -   Kurze Lebensdauer (Access-Token) + Refresh-Token-Logik.
-   **Agent-Token**:
    -   Konfiguriert über Umgebungsvariablen (`AGENT_TOKEN`).
    -   Dient der internen Kommunikation zwischen Agenten.

### 2. Multi-Faktor-Authentifizierung (MFA)
-   **TOTP**: Zeitbasierte Einmalpasswörter (z.B. Google Authenticator).
-   **Setup-Flow**: `/mfa/setup` generiert Secret -> `/mfa/verify` bestätigt Aktivierung.
-   **Backup-Codes**: Werden bei Aktivierung generiert, falls das TOTP-Gerät verloren geht.

### 3. Rollenkonzept
-   **`admin`**: Voller Zugriff auf alle Endpunkte (Benutzerverwaltung, Team-Konfiguration, Löschoperationen).
-   **`user`**: Eingeschränkter Zugriff (Task-Erstellung, Ausführung, eigene Einstellungen).

---

## 🤖 LLM-Integration

Die Kommunikation mit LLMs erfolgt abstrahiert über Provider-Klassen:
-   **Timeout**: Kann per Request gesteuert werden oder nutzt globalen Default (`60s`).
-   **Tool-Calling**: Agenten können vordefinierte Python-Funktionen oder Shell-Befehle vorschlagen, die nach Benutzerfreigabe ausgeführt werden.

---

## 📝 Logging & Audit

-   **Audit-Logs (`AuditLogDB`)**: Protokollierung kritischer Aktionen (Login, Passwortänderung, Admin-Aktionen) mit IP und Zeitstempel.
-   **Terminal-Logs**: Werden im Dateisystem (`data/terminal_log.jsonl`) und in der Task-Historie gespeichert.

---

*Für Details zur API-Nutzung siehe [api-spec.md](../api-spec.md).*

## Hybrid-RAG Integration

- `agent/hybrid_orchestrator.py` fuehrt drei Engines zusammen:
  - Aider-inspirierte Repository-Map (Tree-Sitter + inkrementeller Cache),
  - Vibe-inspirierte agentische Skillsuche (`rg`/`ls`/`cat`, budgetiert),
  - LlamaIndex-basierte semantische Suche mit persistenter Ingestion.
- `ContextManager` waehlt den Mix je Anfrage und begrenzt den Kontext ueber Zeichen- und Token-Budget.
- Security: Sensible Inhalte werden redigiert; agentische Shell-Aufrufe nutzen eine Allowlist und sanitisierten Input.
- API:
  - `POST /api/sgpt/context`
  - `POST /api/sgpt/execute` mit `use_hybrid_context=true`

## CLI-Backend Support

- Direkt unterstuetzte CLI-Backends:
  - `sgpt` (Shell-GPT)
  - `opencode` (OpenCode)
  - `aider` (Aider CLI)
  - `mistral_code` (Mistral Code CLI)
- Runtime-Discovery:
  - `GET /api/sgpt/backends` liefert unterstützte Backends und Capabilities.
