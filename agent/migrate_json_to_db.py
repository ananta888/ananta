import json
import os

from sqlmodel import Session

from agent.common.mfa import encrypt_secret
from agent.config import settings
from agent.database import engine, init_db
from agent.db_models import AgentInfoDB, ConfigDB, ScheduledTaskDB, TaskDB, TeamDB, TemplateDB, UserDB


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate_folder(folder_path, session):
    print(f"Migriere Ordner: {folder_path}")

    # 1. Users migration
    users_json = read_json(os.path.join(folder_path, "users.json"), {})
    for username, info in users_json.items():
        user = session.get(UserDB, username)
        if not user:
            session.add(
                UserDB(
                    username=username,
                    password_hash=info["password"],
                    role=info.get("role", "user"),
                    mfa_secret=encrypt_secret(info.get("mfa_secret")),
                    mfa_enabled=info.get("mfa_enabled", False),
                )
            )

    # 2. Agents migration
    agents_json = read_json(os.path.join(folder_path, "agents.json"), {})
    if isinstance(agents_json, dict):
        for key, a in agents_json.items():
            if not isinstance(a, dict):
                continue
            url = a.get("url") or key
            agent = session.get(AgentInfoDB, url)
            if not agent:
                if "name" not in a:
                    # Versuche Name aus Key oder URL zu bestimmen
                    a["name"] = a.get("name") or key
                if "url" not in a:
                    a["url"] = url
                session.add(AgentInfoDB(**a))
    elif isinstance(agents_json, list):
        for a in agents_json:
            if isinstance(a, dict) and "url" in a:
                agent = session.get(AgentInfoDB, a["url"])
                if not agent:
                    if "name" not in a:
                        a["name"] = a["url"]
                    session.add(AgentInfoDB(**a))

    # 3. Teams migration
    teams_json = read_json(os.path.join(folder_path, "teams.json"), [])
    for t in teams_json:
        team = session.get(TeamDB, t["id"])
        if not team:
            session.add(TeamDB(**t))

    # 4. Templates migration
    templates_json = read_json(os.path.join(folder_path, "templates.json"), [])
    for t in templates_json:
        template = session.get(TemplateDB, t["id"])
        if not template:
            session.add(TemplateDB(**t))

    # 5. Tasks migration (Normale Tasks)
    tasks_json = read_json(os.path.join(folder_path, "tasks.json"), {})
    if isinstance(tasks_json, dict):
        for tid, tdata in tasks_json.items():
            task = session.get(TaskDB, tid)
            if not task:
                # Wir müssen sicherstellen, dass tdata alle Felder für TaskDB hat oder sie filtern
                try:
                    session.add(TaskDB(**tdata))
                except Exception as e:
                    print(f"Fehler bei Task-Migration {tid}: {e}")

    # 6. Scheduled Tasks migration
    sched_tasks_json = read_json(os.path.join(folder_path, "scheduled_tasks.json"), [])
    if isinstance(sched_tasks_json, list):
        for t in sched_tasks_json:
            stask = session.get(ScheduledTaskDB, t["id"])
            if not stask:
                session.add(ScheduledTaskDB(**t))

    # 7. Config migration
    config_json = read_json(os.path.join(folder_path, "config.json"), {})
    for key, value in config_json.items():
        cfg = session.get(ConfigDB, key)
        if not cfg:
            session.add(ConfigDB(key=key, value_json=json.dumps(value)))


def migrate():
    print("Starte Migration von JSON zu DB...")
    init_db()

    with Session(engine) as session:
        # Hauptordner migrieren
        migrate_folder(settings.data_dir, session)

        # Unterordner migrieren
        for entry in os.listdir(settings.data_dir):
            full_path = os.path.join(settings.data_dir, entry)
            if os.path.isdir(full_path):
                migrate_folder(full_path, session)

        session.commit()
    print("Migration erfolgreich abgeschlossen.")


if __name__ == "__main__":
    migrate()
