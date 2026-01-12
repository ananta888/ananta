import json
import time
from sqlmodel import Session, select
from agent.database import engine
from agent.db_models import (
    UserDB, AgentInfoDB, TeamDB, TemplateDB, ScheduledTaskDB, 
    ConfigDB, RefreshTokenDB, TaskDB, StatsSnapshotDB, AuditLogDB,
    LoginAttemptDB, PasswordHistoryDB
)
from typing import List, Optional

class UserRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(UserDB)).all()
    
    def get_by_username(self, username: str) -> Optional[UserDB]:
        with Session(engine) as session:
            return session.get(UserDB, username)
    
    def save(self, user: UserDB):
        with Session(engine) as session:
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def delete(self, username: str):
        with Session(engine) as session:
            user = session.get(UserDB, username)
            if user:
                session.delete(user)
                session.commit()
                return True
            return False

class RefreshTokenRepository:
    def get_by_token(self, token: str) -> Optional[RefreshTokenDB]:
        with Session(engine) as session:
            return session.get(RefreshTokenDB, token)

    def save(self, token_obj: RefreshTokenDB):
        with Session(engine) as session:
            session.add(token_obj)
            session.commit()
            session.refresh(token_obj)
            return token_obj

    def delete(self, token: str):
        with Session(engine) as session:
            token_obj = session.get(RefreshTokenDB, token)
            if token_obj:
                session.delete(token_obj)
                session.commit()
                return True
            return False

    def delete_expired(self):
        with Session(engine) as session:
            statement = select(RefreshTokenDB).where(RefreshTokenDB.expires_at < time.time())
            results = session.exec(statement)
            for token_obj in results:
                session.delete(token_obj)
            session.commit()

    def delete_by_username(self, username: str):
        with Session(engine) as session:
            from sqlmodel import delete
            statement = delete(RefreshTokenDB).where(RefreshTokenDB.username == username)
            session.exec(statement)
            session.commit()

class AgentRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(AgentInfoDB)).all()
    
    def get_by_url(self, url: str):
        with Session(engine) as session:
            return session.get(AgentInfoDB, url)
    
    def save(self, agent: AgentInfoDB):
        with Session(engine) as session:
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

class TeamRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TeamDB)).all()
    
    def get_by_id(self, team_id: str):
        with Session(engine) as session:
            return session.get(TeamDB, team_id)
    
    def save(self, team: TeamDB):
        with Session(engine) as session:
            session.add(team)
            session.commit()
            session.refresh(team)
            return team

    def delete(self, team_id: str):
        with Session(engine) as session:
            team = session.get(TeamDB, team_id)
            if team:
                session.delete(team)
                session.commit()
                return True
            return False

class TemplateRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TemplateDB)).all()
    
    def get_by_id(self, template_id: str):
        with Session(engine) as session:
            return session.get(TemplateDB, template_id)
    
    def save(self, template: TemplateDB):
        with Session(engine) as session:
            session.add(template)
            session.commit()
            session.refresh(template)
            return template

    def delete(self, template_id: str):
        with Session(engine) as session:
            template = session.get(TemplateDB, template_id)
            if template:
                session.delete(template)
                session.commit()
                return True
            return False

class ScheduledTaskRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(ScheduledTaskDB)).all()
    
    def get_by_id(self, task_id: str):
        with Session(engine) as session:
            return session.get(ScheduledTaskDB, task_id)
    
    def save(self, task: ScheduledTaskDB):
        with Session(engine) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(engine) as session:
            task = session.get(ScheduledTaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

class TaskRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TaskDB)).all()
    
    def get_by_id(self, task_id: str) -> Optional[TaskDB]:
        with Session(engine) as session:
            return session.get(TaskDB, task_id)
    
    def save(self, task: TaskDB):
        with Session(engine) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

class ConfigRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(ConfigDB)).all()
    
    def get_by_key(self, key: str):
        with Session(engine) as session:
            return session.get(ConfigDB, key)
    
    def save(self, config: ConfigDB):
        with Session(engine) as session:
            session.add(config)
            session.commit()
            session.refresh(config)
            return config

class StatsRepository:
    def get_all(self, limit: Optional[int] = None, offset: int = 0) -> List[StatsSnapshotDB]:
        with Session(engine) as session:
            statement = select(StatsSnapshotDB).order_by(StatsSnapshotDB.timestamp.desc())
            if limit is not None:
                statement = statement.offset(offset).limit(limit)
            elif offset > 0:
                statement = statement.offset(offset)
            return session.exec(statement).all()

    def save(self, snapshot: StatsSnapshotDB):
        with Session(engine) as session:
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            return snapshot

    def delete_old(self, keep_count: int):
        with Session(engine) as session:
            # Wir holen die IDs der Snapshots, die wir behalten wollen
            statement = select(StatsSnapshotDB.id).order_by(StatsSnapshotDB.timestamp.desc()).limit(keep_count)
            ids_to_keep = session.exec(statement).all()
            
            # Alle anderen löschen
            from sqlmodel import delete
            delete_statement = delete(StatsSnapshotDB).where(StatsSnapshotDB.id.not_in(ids_to_keep))
            session.exec(delete_statement)
            session.commit()

class AuditLogRepository:
    def get_all(self, limit: int = 100, offset: int = 0):
        with Session(engine) as session:
            statement = select(AuditLogDB).order_by(AuditLogDB.timestamp.desc()).limit(limit).offset(offset)
            return session.exec(statement).all()
    
    def save(self, log_entry: AuditLogDB):
        with Session(engine) as session:
            session.add(log_entry)
            session.commit()
            session.refresh(log_entry)
            return log_entry

class LoginAttemptRepository:
    def get_recent_count(self, ip: str, window_seconds: int = 60) -> int:
        now = time.time()
        with Session(engine) as session:
            statement = select(LoginAttemptDB).where(
                LoginAttemptDB.ip == ip,
                LoginAttemptDB.timestamp > now - window_seconds
            )
            results = session.exec(statement)
            return len(results.all())

    def record_attempt(self, ip: str):
        attempt = LoginAttemptDB(ip=ip)
        with Session(engine) as session:
            session.add(attempt)
            session.commit()
            
    def delete_by_ip(self, ip: str):
         with Session(engine) as session:
            from sqlmodel import delete
            statement = delete(LoginAttemptDB).where(LoginAttemptDB.ip == ip)
            session.exec(statement)
            session.commit()

    def clear_all(self):
        with Session(engine) as session:
            from sqlmodel import delete
            session.exec(delete(LoginAttemptDB))
            session.commit()

class PasswordHistoryRepository:
    def get_by_username(self, username: str, limit: int = 3) -> List[PasswordHistoryDB]:
        with Session(engine) as session:
            statement = select(PasswordHistoryDB).where(
                PasswordHistoryDB.username == username
            ).order_by(PasswordHistoryDB.created_at.desc()).limit(limit)
            return session.exec(statement).all()

    def save(self, history_entry: PasswordHistoryDB):
        with Session(engine) as session:
            session.add(history_entry)
            session.commit()
            session.refresh(history_entry)
            return history_entry
    
    def delete_by_username(self, username: str):
        with Session(engine) as session:
            from sqlmodel import delete
            statement = delete(PasswordHistoryDB).where(PasswordHistoryDB.username == username)
            session.exec(statement)
            session.commit()

# Singletons für Repositories
user_repo = UserRepository()
refresh_token_repo = RefreshTokenRepository()
agent_repo = AgentRepository()
team_repo = TeamRepository()
template_repo = TemplateRepository()
scheduled_task_repo = ScheduledTaskRepository()
task_repo = TaskRepository()
config_repo = ConfigRepository()
stats_repo = StatsRepository()
audit_repo = AuditLogRepository()
login_attempt_repo = LoginAttemptRepository()
password_history_repo = PasswordHistoryRepository()
