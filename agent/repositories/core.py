from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import AgentInfoDB, ConfigDB, PlaybookDB, ScheduledTaskDB, TeamDB, TemplateDB


class AgentRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(AgentInfoDB)).all()

    def get_by_url(self, url: str):
        with Session(engine) as session:
            return session.get(AgentInfoDB, url)

    def save(self, agent: AgentInfoDB):
        with Session(engine) as session:
            merged = session.merge(agent)
            session.commit()
            session.refresh(merged)
            return merged


class TeamRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TeamDB)).all()

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(TeamDB).where(TeamDB.name == name)).first()

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

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(TemplateDB).where(TemplateDB.name == name)).first()

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


class ConfigRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(ConfigDB)).all()

    def get_by_key(self, key: str):
        with Session(engine) as session:
            return session.get(ConfigDB, key)

    def save(self, config: ConfigDB):
        with Session(engine) as session:
            merged = session.merge(config)
            session.commit()
            session.refresh(merged)
            return merged


class PlaybookRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(PlaybookDB)).all()

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(PlaybookDB).where(PlaybookDB.name == name)).first()

    def get_by_id(self, playbook_id: str):
        with Session(engine) as session:
            return session.get(PlaybookDB, playbook_id)

    def save(self, playbook: PlaybookDB):
        with Session(engine) as session:
            merged = session.merge(playbook)
            session.commit()
            session.refresh(merged)
            return merged

    def delete(self, playbook_id: str):
        with Session(engine) as session:
            playbook = session.get(PlaybookDB, playbook_id)
            if playbook:
                session.delete(playbook)
                session.commit()
                return True
            return False
