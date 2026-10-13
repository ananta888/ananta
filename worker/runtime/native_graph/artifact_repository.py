"""Read-only local SQL artifact adapter; no Hub repositories or task control."""

from sqlalchemy.orm import Session


class LocalArtifactRows:
    def __init__(self, *, engine, artifact_model, version_model):
        self._engine, self._artifact_model, self._version_model = engine, artifact_model, version_model

    def load_artifact(self, identity):
        return self._load(self._artifact_model, identity)

    def load_version(self, identity):
        return self._load(self._version_model, identity)

    def _load(self, model, identity):
        with Session(self._engine) as session:
            return session.get(model, identity)
