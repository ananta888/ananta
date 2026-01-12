import json
from sqlmodel import Session, select
from agent.database import engine
from agent.db_models import UserDB, AgentInfoDB, TeamDB, TemplateDB, ScheduledTaskDB, ConfigDB
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

# Singletons für Repositories
user_repo = UserRepository()
agent_repo = AgentRepository()
team_repo = TeamRepository()
