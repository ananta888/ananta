from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    BlueprintArtifactDB,
    BlueprintRoleDB,
    RoleDB,
    TeamBlueprintDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
)


class TeamTypeRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TeamTypeDB)).all()

    def get_by_id(self, team_type_id: str):
        with Session(engine) as session:
            return session.get(TeamTypeDB, team_type_id)

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(TeamTypeDB).where(TeamTypeDB.name == name)).first()

    def save(self, team_type: TeamTypeDB):
        with Session(engine) as session:
            session.add(team_type)
            session.commit()
            session.refresh(team_type)
            return team_type

    def delete(self, team_type_id: str):
        with Session(engine) as session:
            team_type = session.get(TeamTypeDB, team_type_id)
            if team_type:
                session.delete(team_type)
                session.commit()
                return True
            return False


class RoleRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(RoleDB)).all()

    def get_by_id(self, role_id: str):
        with Session(engine) as session:
            return session.get(RoleDB, role_id)

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(RoleDB).where(RoleDB.name == name)).first()

    def save(self, role: RoleDB):
        with Session(engine) as session:
            session.add(role)
            session.commit()
            session.refresh(role)
            return role

    def delete(self, role_id: str):
        with Session(engine) as session:
            role = session.get(RoleDB, role_id)
            if role:
                session.delete(role)
                session.commit()
                return True
            return False


class TeamMemberRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TeamMemberDB)).all()

    def get_by_team(self, team_id: str):
        with Session(engine) as session:
            return session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team_id)).all()

    def save(self, member: TeamMemberDB):
        with Session(engine) as session:
            session.add(member)
            session.commit()
            session.refresh(member)
            return member

    def delete(self, member_id: str):
        with Session(engine) as session:
            member = session.get(TeamMemberDB, member_id)
            if member:
                session.delete(member)
                session.commit()
                return True
            return False

    def delete_by_team(self, team_id: str):
        with Session(engine) as session:
            from sqlmodel import delete

            session.exec(delete(TeamMemberDB).where(TeamMemberDB.team_id == team_id))
            session.commit()


class TeamBlueprintRepository:
    def get_all(self):
        with Session(engine) as session:
            statement = select(TeamBlueprintDB).order_by(TeamBlueprintDB.is_seed.desc(), TeamBlueprintDB.name.asc())
            return session.exec(statement).all()

    def get_by_id(self, blueprint_id: str):
        with Session(engine) as session:
            return session.get(TeamBlueprintDB, blueprint_id)

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == name)).first()

    def save(self, blueprint: TeamBlueprintDB):
        with Session(engine) as session:
            session.add(blueprint)
            session.commit()
            session.refresh(blueprint)
            return blueprint

    def delete(self, blueprint_id: str):
        with Session(engine) as session:
            blueprint = session.get(TeamBlueprintDB, blueprint_id)
            if blueprint:
                session.delete(blueprint)
                session.commit()
                return True
            return False


class BlueprintRoleRepository:
    def get_by_blueprint(self, blueprint_id: str) -> List[BlueprintRoleDB]:
        with Session(engine) as session:
            statement = (
                select(BlueprintRoleDB)
                .where(BlueprintRoleDB.blueprint_id == blueprint_id)
                .order_by(BlueprintRoleDB.sort_order.asc(), BlueprintRoleDB.name.asc())
            )
            return session.exec(statement).all()

    def get_by_id(self, blueprint_role_id: str):
        with Session(engine) as session:
            return session.get(BlueprintRoleDB, blueprint_role_id)

    def save(self, blueprint_role: BlueprintRoleDB):
        with Session(engine) as session:
            session.add(blueprint_role)
            session.commit()
            session.refresh(blueprint_role)
            return blueprint_role

    def delete_by_blueprint(self, blueprint_id: str):
        with Session(engine) as session:
            from sqlmodel import delete

            session.exec(delete(BlueprintRoleDB).where(BlueprintRoleDB.blueprint_id == blueprint_id))
            session.commit()


class BlueprintArtifactRepository:
    def get_by_blueprint(self, blueprint_id: str) -> List[BlueprintArtifactDB]:
        with Session(engine) as session:
            statement = (
                select(BlueprintArtifactDB)
                .where(BlueprintArtifactDB.blueprint_id == blueprint_id)
                .order_by(BlueprintArtifactDB.sort_order.asc(), BlueprintArtifactDB.title.asc())
            )
            return session.exec(statement).all()

    def save(self, artifact: BlueprintArtifactDB):
        with Session(engine) as session:
            session.add(artifact)
            session.commit()
            session.refresh(artifact)
            return artifact

    def delete_by_blueprint(self, blueprint_id: str):
        with Session(engine) as session:
            from sqlmodel import delete

            session.exec(delete(BlueprintArtifactDB).where(BlueprintArtifactDB.blueprint_id == blueprint_id))
            session.commit()


class TeamTypeRoleLinkRepository:
    def get_by_team_type(self, team_type_id: str) -> List[TeamTypeRoleLink]:
        with Session(engine) as session:
            return session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.team_type_id == team_type_id)).all()

    def get_allowed_role_ids(self, team_type_id: str) -> List[str]:
        links = self.get_by_team_type(team_type_id)
        return [link.role_id for link in links]

    def save(self, link: TeamTypeRoleLink):
        with Session(engine) as session:
            session.add(link)
            session.commit()
            session.refresh(link)
            return link

    def delete(self, team_type_id: str, role_id: str):
        with Session(engine) as session:
            link = session.get(TeamTypeRoleLink, (team_type_id, role_id))
            if link:
                session.delete(link)
                session.commit()
                return True
            return False
