from typing import List, Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import StatsSnapshotDB


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
