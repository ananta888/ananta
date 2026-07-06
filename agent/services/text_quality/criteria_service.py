from __future__ import annotations

import json
from pathlib import Path

from agent.db_models import TextQualityCriteriaSetDB
from agent.services.repository_registry import get_repository_registry

from .models import ContentKind, CriteriaSet


class CriteriaService:
    def default(self, language: str, content_kind: ContentKind) -> CriteriaSet:
        lang = "de" if language.lower().startswith("de") else "en"
        path = Path(__file__).with_name("profiles") / f"{lang}.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        criteria = CriteriaSet(
            version=str(profile["version"]),
            language=lang,
            profile_name=str(profile["profile_name"]),
            content_kinds=[content_kind],
            status="enabled",
            blocked_phrases=list(profile["blocked_phrases"]),
            thresholds=dict(profile["thresholds"]),
        )
        criteria.checksum = criteria.canonical_checksum()
        return criteria

    def active(self, language: str, content_kind: ContentKind) -> CriteriaSet:
        profile = f"critical_editor_{'de' if language.startswith('de') else 'en'}"
        row = get_repository_registry().text_quality_criteria_set_repo.get_active(
            profile, language[:2], content_kind.value
        )
        if row is None:
            return self.default(language, content_kind)
        return CriteriaSet.model_validate(
            {
                **dict(row.criteria_payload or {}),
                "id": row.id,
                "version": row.version,
                "language": row.language,
                "profile_name": row.profile_name,
                "content_kinds": row.content_kinds,
                "status": row.status,
                "checksum": row.checksum,
                "source_refs": row.source_refs,
                "created_by": row.created_by,
                "created_at": row.created_at,
            }
        )

    def create(self, criteria: CriteriaSet) -> TextQualityCriteriaSetDB:
        checksum = criteria.checksum or criteria.canonical_checksum()
        payload = criteria.model_dump(
            mode="json",
            exclude={
                "id",
                "version",
                "language",
                "profile_name",
                "content_kinds",
                "status",
                "checksum",
                "source_refs",
                "created_by",
                "created_at",
            },
        )
        return get_repository_registry().text_quality_criteria_set_repo.save(
            TextQualityCriteriaSetDB(
                id=criteria.id,
                version=criteria.version,
                language=criteria.language,
                profile_name=criteria.profile_name,
                content_kinds=[kind.value for kind in criteria.content_kinds],
                status=criteria.status,
                criteria_payload=payload,
                checksum=checksum,
                source_refs=criteria.source_refs,
                created_by=criteria.created_by,
            )
        )

    def set_status(self, criteria_id: str, status: str):
        return get_repository_registry().text_quality_criteria_set_repo.set_status(criteria_id, status)


_SERVICE = CriteriaService()


def get_criteria_service() -> CriteriaService:
    return _SERVICE
