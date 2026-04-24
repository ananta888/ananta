import time
from typing import Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import InstructionOverlayDB, UserInstructionProfileDB


class UserInstructionProfileRepository:
    def get_by_id(self, profile_id: str) -> Optional[UserInstructionProfileDB]:
        with Session(engine) as session:
            return session.get(UserInstructionProfileDB, profile_id)

    def list_by_owner(self, owner_username: str, *, include_inactive: bool = True) -> list[UserInstructionProfileDB]:
        with Session(engine) as session:
            statement = select(UserInstructionProfileDB).where(UserInstructionProfileDB.owner_username == owner_username)
            if not include_inactive:
                statement = statement.where(UserInstructionProfileDB.is_active.is_(True))
            statement = statement.order_by(UserInstructionProfileDB.updated_at.desc())
            return session.exec(statement).all()

    def get_active_for_owner(self, owner_username: str) -> Optional[UserInstructionProfileDB]:
        with Session(engine) as session:
            statement = (
                select(UserInstructionProfileDB)
                .where(UserInstructionProfileDB.owner_username == owner_username, UserInstructionProfileDB.is_active.is_(True))
                .order_by(UserInstructionProfileDB.is_default.desc(), UserInstructionProfileDB.updated_at.desc())
            )
            return session.exec(statement).first()

    def save(self, profile: UserInstructionProfileDB) -> UserInstructionProfileDB:
        with Session(engine) as session:
            merged = session.merge(profile)
            merged.updated_at = time.time()
            session.add(merged)
            session.commit()
            session.refresh(merged)
            return merged

    def delete(self, profile_id: str) -> bool:
        with Session(engine) as session:
            profile = session.get(UserInstructionProfileDB, profile_id)
            if profile is None:
                return False
            session.delete(profile)
            session.commit()
            return True

    def set_default_profile(self, owner_username: str, profile_id: str) -> Optional[UserInstructionProfileDB]:
        with Session(engine) as session:
            profiles = session.exec(
                select(UserInstructionProfileDB).where(UserInstructionProfileDB.owner_username == owner_username)
            ).all()
            selected: Optional[UserInstructionProfileDB] = None
            now = time.time()
            for profile in profiles:
                should_select = str(profile.id) == str(profile_id)
                profile.is_default = should_select
                profile.is_active = should_select
                profile.updated_at = now
                session.add(profile)
                if should_select:
                    selected = profile
            session.commit()
            if selected is not None:
                session.refresh(selected)
            return selected


class InstructionOverlayRepository:
    def get_by_id(self, overlay_id: str) -> Optional[InstructionOverlayDB]:
        with Session(engine) as session:
            return session.get(InstructionOverlayDB, overlay_id)

    def list_by_owner(
        self,
        owner_username: str,
        *,
        include_inactive: bool = True,
        attachment_kind: str | None = None,
        attachment_id: str | None = None,
        include_expired: bool = True,
        now_ts: float | None = None,
    ) -> list[InstructionOverlayDB]:
        now = float(now_ts or time.time())
        with Session(engine) as session:
            statement = select(InstructionOverlayDB).where(InstructionOverlayDB.owner_username == owner_username)
            if not include_inactive:
                statement = statement.where(InstructionOverlayDB.is_active.is_(True))
            if attachment_kind:
                statement = statement.where(InstructionOverlayDB.attachment_kind == attachment_kind)
            if attachment_id:
                statement = statement.where(InstructionOverlayDB.attachment_id == attachment_id)
            if not include_expired:
                statement = statement.where(
                    (InstructionOverlayDB.expires_at.is_(None)) | (InstructionOverlayDB.expires_at > now)
                )
            statement = statement.order_by(InstructionOverlayDB.updated_at.desc())
            return session.exec(statement).all()

    def save(self, overlay: InstructionOverlayDB) -> InstructionOverlayDB:
        with Session(engine) as session:
            merged = session.merge(overlay)
            merged.updated_at = time.time()
            session.add(merged)
            session.commit()
            session.refresh(merged)
            return merged

    def delete(self, overlay_id: str) -> bool:
        with Session(engine) as session:
            overlay = session.get(InstructionOverlayDB, overlay_id)
            if overlay is None:
                return False
            session.delete(overlay)
            session.commit()
            return True
