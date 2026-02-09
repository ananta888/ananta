# Backend-Dokumentation

## Übersicht

Das Ananta-Backend ist in Python mit Flask implementiert und nutzt SQLModel als ORM für die Datenbankinteraktion. Die Authentifizierung erfolgt über JWT-Tokens (JSON Web Tokens) mit HS256-Algorithmus.

## Database Models

Das Backend verwendet SQLModel (basierend auf SQLAlchemy und Pydantic) für die Datenbankmodelle. Alle Models sind in `agent/db_models.py` definiert.

### Kern-Models

#### User & Authentication
- **UserDB**: Benutzerverwaltung mit Passwort-Hash, Rollen, MFA-Support und Lockout-Mechanismus
- **LoginAttemptDB**: Tracking von Login-Versuchen (IP-basiert)
- **BannedIPDB**: Gesperrte IP-Adressen mit Ablaufzeit
- **RefreshTokenDB**: Refresh-Tokens für Token-Erneuerung
- **PasswordHistoryDB**: Historie der Passwort-Hashes zur Verhinderung von Wiederverwendung

#### Agents & Teams
- **AgentInfoDB**: Registrierte Agents mit URL, Name, Rolle, Token und Status
- **TeamDB**: Teams mit Name, Beschreibung, Team-Typ und Aktiv-Status
- **TeamMemberDB**: Zuordnung von Agents zu Teams mit Rollen
- **TeamTypeDB**: Team-Typen (z.B. "Development", "QA")
- **RoleDB**: Rollen innerhalb von Teams (z.B. "Backend Developer", "QA Engineer")
- **TeamTypeRoleLink**: Verknüpfung zwischen Team-Typen und verfügbaren Rollen

#### Tasks & Templates
- **TaskDB**: Aktive Tasks mit Status, Priorität, Zuweisung, History und Callback-URLs
- **ArchivedTaskDB**: Archivierte Tasks (gleiche Struktur wie TaskDB)
- **TemplateDB**: Prompt-Templates für AI-Agents
- **ScheduledTaskDB**: Geplante wiederkehrende Tasks

#### System
- **ConfigDB**: Key-Value-Store für Konfiguration (JSON-serialisiert)
- **StatsSnapshotDB**: Historische Statistiken (Agents, Tasks, Resources)
- **AuditLogDB**: Audit-Logs für sicherheitsrelevante Aktionen

### Beispiel: Model-Definition

```python
from sqlmodel import SQLModel, Field, Column, JSON
from typing import Optional, List
import uuid

class TaskDB(SQLModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "todo"
    priority: str = "Medium"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id")
    assigned_agent_url: Optional[str] = Field(default=None, foreign_key="agents.url")
    history: List[dict] = Field(default=[], sa_column=Column(JSON))
```

## ORM-Usage (SQLModel)

### Setup & Session-Management

Die Datenbank-Verbindung wird in `agent/database.py` konfiguriert:

```python
from sqlmodel import create_engine, Session, SQLModel

# Engine erstellen (SQLite, PostgreSQL, etc.)
engine = create_engine(database_url, echo=False)

# Tabellen erstellen
SQLModel.metadata.create_all(engine)

# Session für Datenbankoperationen
with Session(engine) as session:
    # Operationen hier
    session.commit()
```

### CRUD-Operationen

#### Create
```python
from agent.db_models import TaskDB
from sqlmodel import Session

with Session(engine) as session:
    task = TaskDB(
        id="task-123",
        title="Neue Aufgabe",
        description="Beschreibung",
        status="todo"
    )
    session.add(task)
    session.commit()
    session.refresh(task)  # Lädt generierte Felder
```

#### Read
```python
from sqlmodel import select

# Einzelnes Objekt
with Session(engine) as session:
    task = session.get(TaskDB, "task-123")
    
# Query mit Filter
with Session(engine) as session:
    statement = select(TaskDB).where(TaskDB.status == "todo")
    tasks = session.exec(statement).all()
```

#### Update
```python
with Session(engine) as session:
    task = session.get(TaskDB, "task-123")
    if task:
        task.status = "in_progress"
        task.updated_at = time.time()
        session.add(task)
        session.commit()
```

#### Delete
```python
with Session(engine) as session:
    task = session.get(TaskDB, "task-123")
    if task:
        session.delete(task)
        session.commit()
```

### Relationships & Joins

SQLModel unterstützt Foreign Keys und Joins:

```python
# Tasks mit zugewiesenem Agent laden
statement = (
    select(TaskDB, AgentInfoDB)
    .join(AgentInfoDB, TaskDB.assigned_agent_url == AgentInfoDB.url)
    .where(TaskDB.status == "in_progress")
)
results = session.exec(statement).all()
```

## API-Authentication

Das Backend nutzt JWT (JSON Web Tokens) für die Authentifizierung. Es gibt zwei Token-Typen:

### Token-Typen

1. **AGENT_TOKEN**: Statischer oder JWT-Token für Agent-zu-Agent-Kommunikation
   - Berechtigt zu allen Operationen
   - Wird in `Authorization: Bearer <token>` Header oder `?token=<token>` Query-Parameter übergeben

2. **User-JWT**: Dynamischer JWT für Benutzer-Authentifizierung
   - Signiert mit `settings.secret_key`
   - Enthält Payload: `{"username": "...", "role": "admin|user", "exp": ...}`
   - Wird nach Login generiert

### JWT-Generierung

```python
from agent.auth import generate_token

# User-JWT erstellen
payload = {"username": "admin", "role": "admin"}
token = generate_token(payload, settings.secret_key, expires_in=3600)
```

### Authentication-Middleware

Das Backend bietet drei Decorator-Funktionen in `agent/auth.py`:

#### 1. `@check_auth` - Agent- oder User-Authentifizierung

Prüft auf gültigen AGENT_TOKEN oder User-JWT:

```python
from agent.auth import check_auth
from flask import g

@app.route("/api/tasks", methods=["POST"])
@check_auth
def create_task():
    # g.is_admin ist True, wenn AGENT_TOKEN oder Admin-User
    # g.user enthält User-Payload bei User-JWT
    if not g.is_admin:
        return {"error": "forbidden"}, 403
    # ... Task erstellen
```

#### 2. `@check_user_auth` - Nur User-JWT

Erfordert einen gültigen User-JWT (kein AGENT_TOKEN):

```python
from agent.auth import check_user_auth

@app.route("/api/profile", methods=["GET"])
@check_user_auth
def get_profile():
    # g.user enthält {"username": "...", "role": "..."}
    # g.is_admin ist True bei role="admin"
    username = g.user["username"]
    return {"username": username}
```

#### 3. `@admin_required` - Admin-Rechte erforderlich

Prüft auf Admin-Rechte (via AGENT_TOKEN oder User-Role):

```python
from agent.auth import admin_required

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def list_users():
    # Nur für Admins zugänglich
    # g.is_admin ist garantiert True
    return {"users": [...]}
```

### Middleware-Beispiel: Vollständiger Endpoint

```python
from flask import Flask, request, g, jsonify
from agent.auth import check_auth, admin_required
from agent.db_models import TaskDB
from agent.database import get_session

app = Flask(__name__)

@app.route("/api/tasks", methods=["POST"])
@check_auth
def create_task():
    """Erstellt einen neuen Task (erfordert Authentifizierung)."""
    data = request.get_json()
    
    with get_session() as session:
        task = TaskDB(
            id=data.get("id"),
            title=data.get("title"),
            description=data.get("description"),
            status="todo"
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        
    return jsonify({"status": "success", "task_id": task.id}), 201

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@admin_required
def delete_task(task_id):
    """Löscht einen Task (nur für Admins)."""
    with get_session() as session:
        task = session.get(TaskDB, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404
        
        session.delete(task)
        session.commit()
        
    return jsonify({"status": "success"}), 200
```

### Token-Rotation

Für erhöhte Sicherheit kann der AGENT_TOKEN rotiert werden:

```python
from agent.auth import rotate_token

# Generiert neuen Token und synchronisiert mit Hub
new_token = rotate_token()
```

## Fehlerbehandlung

Das Backend nutzt `agent.common.errors.api_response` für konsistente API-Antworten:

```python
from agent.common.errors import api_response

# Erfolg
return api_response(status="success", data={"task_id": "123"}, code=200)

# Fehler
return api_response(status="error", message="Task not found", code=404)
```

## Migrations

Datenbankmigrationen werden mit Alembic verwaltet:

```bash
# Migration erstellen
alembic revision --autogenerate -m "Add new field"

# Migration anwenden
alembic upgrade head

# Migration rückgängig machen
alembic downgrade -1
```

## Weitere Ressourcen

- **SQLModel Dokumentation**: https://sqlmodel.tiangolo.com/
- **PyJWT Dokumentation**: https://pyjwt.readthedocs.io/
- **Flask Dokumentation**: https://flask.palletsprojects.com/
