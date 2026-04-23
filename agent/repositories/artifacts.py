from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    ArtifactDB,
    ArtifactVersionDB,
    ExtractedDocumentDB,
    KnowledgeCollectionDB,
    KnowledgeIndexDB,
    KnowledgeIndexRunDB,
    KnowledgeLinkDB,
)


class ArtifactRepository:
    def get_all(self) -> List[ArtifactDB]:
        with Session(engine) as session:
            statement = select(ArtifactDB).order_by(ArtifactDB.created_at.desc())
            return session.exec(statement).all()

    def get_by_id(self, artifact_id: str):
        with Session(engine) as session:
            return session.get(ArtifactDB, artifact_id)

    def save(self, artifact: ArtifactDB):
        with Session(engine) as session:
            session.add(artifact)
            session.commit()
            session.refresh(artifact)
            return artifact


class ArtifactVersionRepository:
    def get_by_id(self, version_id: str):
        with Session(engine) as session:
            return session.get(ArtifactVersionDB, version_id)

    def get_by_artifact(self, artifact_id: str) -> List[ArtifactVersionDB]:
        with Session(engine) as session:
            statement = (
                select(ArtifactVersionDB)
                .where(ArtifactVersionDB.artifact_id == artifact_id)
                .order_by(ArtifactVersionDB.version_number.desc())
            )
            return session.exec(statement).all()

    def save(self, version: ArtifactVersionDB):
        with Session(engine) as session:
            session.add(version)
            session.commit()
            session.refresh(version)
            return version


class ExtractedDocumentRepository:
    def get_by_artifact(self, artifact_id: str) -> List[ExtractedDocumentDB]:
        with Session(engine) as session:
            statement = (
                select(ExtractedDocumentDB)
                .where(ExtractedDocumentDB.artifact_id == artifact_id)
                .order_by(ExtractedDocumentDB.created_at.desc())
            )
            return session.exec(statement).all()

    def save(self, document: ExtractedDocumentDB):
        with Session(engine) as session:
            session.add(document)
            session.commit()
            session.refresh(document)
            return document


class KnowledgeCollectionRepository:
    def get_all(self) -> List[KnowledgeCollectionDB]:
        with Session(engine) as session:
            statement = select(KnowledgeCollectionDB).order_by(KnowledgeCollectionDB.name.asc())
            return session.exec(statement).all()

    def get_by_id(self, collection_id: str):
        with Session(engine) as session:
            return session.get(KnowledgeCollectionDB, collection_id)

    def get_by_name(self, name: str):
        with Session(engine) as session:
            return session.exec(select(KnowledgeCollectionDB).where(KnowledgeCollectionDB.name == name)).first()

    def save(self, collection: KnowledgeCollectionDB):
        with Session(engine) as session:
            session.add(collection)
            session.commit()
            session.refresh(collection)
            return collection


class KnowledgeLinkRepository:
    def get_by_collection(self, collection_id: str) -> List[KnowledgeLinkDB]:
        with Session(engine) as session:
            statement = (
                select(KnowledgeLinkDB)
                .where(KnowledgeLinkDB.collection_id == collection_id)
                .order_by(KnowledgeLinkDB.created_at.desc())
            )
            return session.exec(statement).all()

    def get_by_artifact(self, artifact_id: str) -> List[KnowledgeLinkDB]:
        with Session(engine) as session:
            statement = select(KnowledgeLinkDB).where(KnowledgeLinkDB.artifact_id == artifact_id)
            return session.exec(statement).all()

    def save(self, link: KnowledgeLinkDB):
        with Session(engine) as session:
            session.add(link)
            session.commit()
            session.refresh(link)
            return link


class KnowledgeIndexRepository:
    def get_by_id(self, knowledge_index_id: str):
        with Session(engine) as session:
            return session.get(KnowledgeIndexDB, knowledge_index_id)

    def list_completed(self, *, source_scope: str | None = None) -> List[KnowledgeIndexDB]:
        with Session(engine) as session:
            statement = select(KnowledgeIndexDB).where(KnowledgeIndexDB.status == "completed")
            if source_scope:
                statement = statement.where(KnowledgeIndexDB.source_scope == source_scope)
            statement = statement.order_by(KnowledgeIndexDB.updated_at.desc())
            return session.exec(statement).all()

    def get_by_scope(self, *, source_scope: str, scope_id: str):
        normalized_scope = str(source_scope or "").strip().lower() or "artifact"
        if normalized_scope == "artifact":
            statement = (
                select(KnowledgeIndexDB)
                .where(KnowledgeIndexDB.source_scope == "artifact")
                .where(KnowledgeIndexDB.artifact_id == scope_id)
                .order_by(KnowledgeIndexDB.updated_at.desc())
            )
        else:
            statement = (
                select(KnowledgeIndexDB)
                .where(KnowledgeIndexDB.source_scope == normalized_scope)
                .where(KnowledgeIndexDB.collection_id == scope_id)
                .order_by(KnowledgeIndexDB.updated_at.desc())
            )
        with Session(engine) as session:
            return session.exec(statement).first()

    def get_by_artifact(self, artifact_id: str):
        with Session(engine) as session:
            statement = (
                select(KnowledgeIndexDB)
                .where(KnowledgeIndexDB.artifact_id == artifact_id)
                .where(KnowledgeIndexDB.source_scope == "artifact")
                .order_by(KnowledgeIndexDB.updated_at.desc())
            )
            return session.exec(statement).first()

    def get_by_collection(self, collection_id: str) -> List[KnowledgeIndexDB]:
        with Session(engine) as session:
            statement = (
                select(KnowledgeIndexDB)
                .where(KnowledgeIndexDB.collection_id == collection_id)
                .order_by(KnowledgeIndexDB.updated_at.desc())
            )
            return session.exec(statement).all()

    def save(self, knowledge_index: KnowledgeIndexDB):
        with Session(engine) as session:
            session.add(knowledge_index)
            session.commit()
            session.refresh(knowledge_index)
            return knowledge_index


class KnowledgeIndexRunRepository:
    def get_by_id(self, run_id: str):
        with Session(engine) as session:
            return session.get(KnowledgeIndexRunDB, run_id)

    def get_by_knowledge_index(self, knowledge_index_id: str) -> List[KnowledgeIndexRunDB]:
        with Session(engine) as session:
            statement = (
                select(KnowledgeIndexRunDB)
                .where(KnowledgeIndexRunDB.knowledge_index_id == knowledge_index_id)
                .order_by(KnowledgeIndexRunDB.created_at.desc())
            )
            return session.exec(statement).all()

    def save(self, run: KnowledgeIndexRunDB):
        with Session(engine) as session:
            session.add(run)
            session.commit()
            session.refresh(run)
            return run
