# Datenbankdokumentation

Ananta nutzt **SQLModel** (basierend auf SQLAlchemy und Pydantic) für die Objekt-Relationale Abbildung (ORM).

## Übersicht der Modelle

Die Modelle sind in `agent/db_models.py` definiert.

### 1. Benutzer & Sicherheit
- **UserDB (`users`)**: Speichert Benutzerinformationen, Passwort-Hashes, Rollen (`admin`, `user`) und MFA-Daten.
- **LoginAttemptDB (`login_attempts`)**: Protokolliert Login-Versuche zur Brute-Force-Prävention.
- **BannedIPDB (`banned_ips`)**: Liste der gesperrten IP-Adressen.
- **RefreshTokenDB (`refresh_tokens`)**: Speichert Refresh-Tokens für die JWT-Authentifizierung.
- **PasswordHistoryDB (`password_history`)**: Historie alter Passwort-Hashes zur Verhinderung von Wiederholungen.

### 2. Agenten & Orchestrierung
- **AgentInfoDB (`agents`)**: Registry der verfügbaren Worker-Agenten (URL, Name, Status, Token).
- **TemplateDB (`templates`)**: Prompt-Templates für die LLM-Kommunikation.

### 3. Teams & Rollen
- **TeamTypeDB (`team_types`)**: Vorlagen für Team-Strukturen.
- **RoleDB (`roles`)**: Definition von Rollen innerhalb eines Teams (z.B. "Frontend-Entwickler").
- **TeamTypeRoleLink**: Verknüpft Team-Typen mit Rollen und Standard-Templates.
- **TeamDB (`teams`)**: Konkrete Instanz eines Teams.
- **TeamMemberDB (`team_members`)**: Zuweisung von Agenten zu Rollen innerhalb eines Teams.

### 4. Tasks
- **TaskDB (`tasks`)**: Aktive Aufgaben mit Status (`todo`, `in_progress`, `done`), Priorität und Zuweisung. Speichert auch die Historie der LLM-Vorschläge und Ausgaben.
- **ArchivedTaskDB (`archived_tasks`)**: Archiv für abgeschlossene oder gelöschte Tasks.
- **ScheduledTaskDB (`scheduled_tasks`)**: Wiederkehrende Aufgaben mit Intervall.

### 5. System & Monitoring
- **ConfigDB (`config`)**: Dynamische Konfigurationseinstellungen im Key-Value-Format (JSON).
- **StatsSnapshotDB (`stats_history`)**: Historische Performance- und Status-Snapshots.
- **AuditLogDB (`audit_logs`)**: Revisionssichere Protokollierung kritischer Aktionen.

## ORM Nutzung

### Initialisierung
Die Datenbank wird in `agent/database.py` initialisiert. Sie unterstützt sowohl **PostgreSQL** als auch **SQLite** (als Fallback).

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

### CRUD Operationen
CRUD-Operationen werden über das **Repository-Pattern** in `agent/repository.py` zentralisiert, um die Logik von den API-Endpunkten zu trennen.

Beispiel für eine Abfrage:
```python
from sqlmodel import Session, select
from agent.database import engine
from agent.db_models import UserDB

with Session(engine) as session:
    statement = select(UserDB).where(UserDB.username == "admin")
    user = session.exec(statement).first()
```
