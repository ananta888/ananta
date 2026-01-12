import os
import json
from sqlmodel import Session, select
from agent.database import engine, init_db
from agent.db_models import UserDB, AgentInfoDB, TeamDB, TemplateDB, ScheduledTaskDB, ConfigDB
from agent.config import settings

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def migrate():
    print("Starte Migration von JSON zu DB...")
    init_db()
    
    with Session(engine) as session:
        # 1. Users migration
        users_json = read_json(os.path.join(settings.data_dir, "users.json"), {})
        for username, info in users_json.items():
            user = session.get(UserDB, username)
            if not user:
                session.add(UserDB(
                    username=username,
                    password_hash=info["password"],
                    role=info.get("role", "user"),
                    mfa_secret=info.get("mfa_secret"),
                    mfa_enabled=info.get("mfa_enabled", False)
                ))
        
        # 2. Agents migration
        agents_json = read_json(os.path.join(settings.data_dir, "agents.json"), [])
        for a in agents_json:
            agent = session.get(AgentInfoDB, a["url"])
            if not agent:
                session.add(AgentInfoDB(**a))
        
        # 3. Teams migration
        teams_json = read_json(os.path.join(settings.data_dir, "teams.json"), [])
        for t in teams_json:
            team = session.get(TeamDB, t["id"])
            if not team:
                session.add(TeamDB(**t))
                
        # 4. Templates migration
        templates_json = read_json(os.path.join(settings.data_dir, "templates.json"), [])
        for t in templates_json:
            template = session.get(TemplateDB, t["id"])
            if not template:
                session.add(TemplateDB(**t))
                
        # 5. Tasks migration
        tasks_json = read_json(os.path.join(settings.data_dir, "tasks.json"), [])
        for t in tasks_json:
            task = session.get(ScheduledTaskDB, t["id"])
            if not task:
                session.add(ScheduledTaskDB(**t))
                
        # 6. Config migration
        config_json = read_json(os.path.join(settings.data_dir, "config.json"), {})
        for key, value in config_json.items():
            cfg = session.get(ConfigDB, key)
            if not cfg:
                session.add(ConfigDB(key=key, value_json=json.dumps(value)))
        
        session.commit()
    print("Migration erfolgreich abgeschlossen.")

if __name__ == "__main__":
    migrate()
