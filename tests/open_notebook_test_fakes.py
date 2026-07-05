"""Shared offline fakes for OpenNotebook importer tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeIngestionService:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.collections: dict[str, SimpleNamespace] = {}
        self._artifact_counter = 0

    def upload_artifact(self, *, filename, content, created_by, media_type=None, collection_name=None):
        self._artifact_counter += 1
        artifact = SimpleNamespace(
            id=f"art-{self._artifact_counter}",
            artifact_metadata={"ingestion_mode": "raw_artifact_store"},
            latest_filename=filename,
            latest_media_type=media_type,
            latest_sha256=f"sha-{self._artifact_counter}",
            size_bytes=len(content),
        )
        collection = None
        if collection_name:
            collection = self.collections.get(collection_name)
            if collection is None:
                collection = SimpleNamespace(id=f"col-{len(self.collections) + 1}", name=collection_name)
                self.collections[collection_name] = collection
        self.uploads.append(
            {
                "filename": filename,
                "content": content,
                "media_type": media_type,
                "collection_name": collection_name,
                "created_by": created_by,
                "artifact_id": artifact.id,
            }
        )
        return artifact, SimpleNamespace(id=f"ver-{self._artifact_counter}"), collection


class FakeArtifactRepo:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    def save(self, artifact):
        self.saved[str(artifact.id)] = artifact
        return artifact

    def get_by_id(self, artifact_id):
        return self.saved.get(str(artifact_id))


class FakeCollectionRepo:
    def __init__(self, ingestion: FakeIngestionService | None = None) -> None:
        self._ingestion = ingestion
        self.saved: dict[str, SimpleNamespace] = {}

    def get_by_name(self, name):
        if self._ingestion is not None and name in self._ingestion.collections:
            return self._ingestion.collections[name]
        return self.saved.get(name)

    def save(self, collection):
        record = SimpleNamespace(id=f"col-extra-{len(self.saved) + 1}", name=collection.name)
        self.saved[collection.name] = record
        if self._ingestion is not None:
            self._ingestion.collections[collection.name] = record
        return record


class FakeLinkRepo:
    def __init__(self) -> None:
        self.links: list[Any] = []

    def get_by_artifact(self, artifact_id):
        return [link for link in self.links if str(getattr(link, "artifact_id", "")) == str(artifact_id)]

    def save(self, link):
        self.links.append(link)
        return link


class FakeIndexRepo:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save(self, knowledge_index):
        if not getattr(knowledge_index, "id", None):
            knowledge_index.id = f"idx-{len(self.saved) + 1}"
        self.saved.append(knowledge_index)
        return knowledge_index

    def list_completed(self):
        return [item for item in self.saved if str(getattr(item, "status", "")) == "completed"]


def fake_knowledge_index_factory(**kwargs):
    kwargs.setdefault("id", None)
    kwargs.setdefault("artifact_id", None)
    return SimpleNamespace(**kwargs)


def build_importer(tmp_path, *, policy=None, ingestion=None):
    from agent.sources.open_notebook_importer import OpenNotebookImporter
    from agent.sources.source_registry import SourceRegistry
    from agent.sources.source_snapshot_store import SourceSnapshotStore

    ingestion = ingestion or FakeIngestionService()
    artifact_repo = FakeArtifactRepo()
    collection_repo = FakeCollectionRepo(ingestion)
    link_repo = FakeLinkRepo()
    index_repo = FakeIndexRepo()
    registry = SourceRegistry(root=tmp_path / "registry")
    snapshots = SourceSnapshotStore(root=tmp_path / "snapshots")
    importer = OpenNotebookImporter(
        ingestion_service=ingestion,
        artifact_repository=artifact_repo,
        knowledge_collection_repository=collection_repo,
        knowledge_link_repository=link_repo,
        knowledge_index_repository=index_repo,
        source_registry=registry,
        snapshot_store=snapshots,
        policy=policy,
        index_root=tmp_path / "indices",
        knowledge_index_factory=fake_knowledge_index_factory,
    )
    return SimpleNamespace(
        importer=importer,
        ingestion=ingestion,
        artifact_repo=artifact_repo,
        collection_repo=collection_repo,
        link_repo=link_repo,
        index_repo=index_repo,
        registry=registry,
        snapshots=snapshots,
    )
