# Datenbankdokumentation

Ananta nutzt **SQLModel** (basierend auf SQLAlchemy und Pydantic) für die Objekt-Relationale Abbildung (ORM).

## Übersicht der Modelle

Die Modelle sind in `agent/db_models.py` definiert.

### 1. Benutzer & Sicherheit
- **UserDB (`users`)**: Speichert Benutzerinformationen (Username, Passwort-Hash, Rolle, MFA-Status/Secret).
- **LoginAttemptDB (`login_attempts`)**: Protokolliert Login-Versuche (IP, Zeitstempel) zur Brute-Force-Prävention.
- **BannedIPDB (`banned_ips`)**: Liste der gesperrten IP-Adressen mit Grund und Dauer.
- **RefreshTokenDB (`refresh_tokens`)**: Speichert aktive Refresh-Tokens für die JWT-Authentifizierung.
- **PasswordHistoryDB (`password_history`)**: Historie alter Passwort-Hashes zur Verhinderung von Passwort-Wiederholungen.

### 2. Agenten & Orchestrierung
- **AgentInfoDB (`agents`)**: Registry der verfügbaren Worker-Agenten (URL, Name, Rolle, Status, Token, Last Seen).
- **TemplateDB (`templates`)**: Prompt-Templates für die LLM-Kommunikation.

### 3. Teams & Rollen
- **TeamTypeDB (`team_types`)**: Vorlagen für Team-Strukturen (z.B. "Standard-Dev-Team").
- **RoleDB (`roles`)**: Definition von Rollen (Name, Beschreibung, Standard-Template).
- **TeamTypeRoleLink**: Verknüpft Team-Typen mit verfügbaren Rollen und Standard-Templates.
- **TeamDB (`teams`)**: Konkrete Instanz eines Teams (Name, Team-Typ, Status).
- **TeamMemberDB (`team_members`)**: Zuweisung von konkreten Agenten zu Rollen innerhalb eines Teams.

### 4. Tasks
- **TaskDB (`tasks`)**: Aktive Aufgaben mit Titel, Beschreibung, Status (`todo`, `in_progress`, `done`), Priorität, Zuweisung (Agent/Rolle) und Historie (Vorschläge, Logs).
- **ArchivedTaskDB (`archived_tasks`)**: Archiv für abgeschlossene Tasks. Identisch aufgebaut wie `TaskDB`.
- **ScheduledTaskDB (`scheduled_tasks`)**: Wiederkehrende Aufgaben mit Kommando und Intervall.

### 5. System & Monitoring
- **ConfigDB (`config`)**: Dynamische Konfigurationseinstellungen im Key-Value-Format (JSON).
- **StatsSnapshotDB (`stats_history`)**: Historische Snapshots von Systemstatistiken (Agents, Tasks, Ressourcen).
- **AuditLogDB (`audit_logs`)**: Revisionssichere Protokollierung kritischer Aktionen (Wer, Wann, Was, IP).

## ORM Nutzung

### Initialisierung
Die Datenbank wird in `agent/database.py` initialisiert. Sie unterstützt sowohl **PostgreSQL** als auch **SQLite**. SQLModel wird verwendet, um die Tabellen automatisch aus den Klassen-Definitionen in `agent/db_models.py` zu erstellen (sofern keine Migrationen genutzt werden).

### Migrationen
Datenbankänderungen werden über **Alembic** verwaltet. Die Migrationsdateien befinden sich im Ordner `migrations/`.

Befehl zum Erstellen einer Migration:
```bash
alembic revision --autogenerate -m "Beschreibung"
```

Befehl zum Anwenden von Migrationen:
```bash
alembic upgrade head
```

### Kompatibilitätsmigration beim Start
Zusätzlich zu Alembic führt `agent/database.py` beim App-Start eine kleine
Schema-Kompatibilitätsprüfung aus (`_ensure_schema_compat`).

Aktuell werden dabei bei Bedarf folgende Spalten automatisch nachgezogen:
- `users.mfa_backup_codes`
- `tasks.depends_on`
- `archived_tasks.depends_on`

Für neue Deployments mit bestehender DB gilt als Rollout-Hinweis:
1. Neue Version deployen und App einmal vollständig starten lassen.
2. Startup-Logs auf Hinweise wie `DB schema missing ...; applying compatibility migration.` prüfen.
3. Danach regulär Healthchecks/E2E ausführen.

### CRUD Operationen & Patterns
CRUD-Operationen werden über das **Repository-Pattern** in `agent/repository.py` zentralisiert. Dies entkoppelt die Persistenzlogik von den API-Endpunkten.

**Vorteile:**
- Testbarkeit: Einfacheres Mocken der Datenbankschicht.
- Konsistenz: Validierungsregeln und Standardwerte werden an einer Stelle verwaltet.

**Beispiel für eine Abfrage:**
```python
from sqlmodel import Session, select
from agent.database import engine
from agent.db_models import UserDB

# Direkt via Session
with Session(engine) as session:
    statement = select(UserDB).where(UserDB.username == "admin")
    user = session.exec(statement).first()

# Via Repository (empfohlen)
from agent.repository import get_user_repo
with Session(engine) as session:
    repo = get_user_repo(session)
    user = repo.get_by_username("admin")
```
