from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class RagSourceProfileLoader:
    """Load RAG source profiles from descriptors and validate governance constraints."""

    def __init__(self, *, schema_path: Path | None = None, repository_root: Path | None = None) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.schema_path = schema_path or (self.repository_root / "schemas" / "domain" / "rag_source_profile.v1.json")
        self._profiles_by_domain: dict[str, list[dict[str, Any]]] = {}

    def load_from_descriptors(self, descriptors: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        schema = self._load_json(self.schema_path)
        validator = Draft202012Validator(schema)
        known_domains = set(descriptors.keys())
        profiles_by_domain: dict[str, list[dict[str, Any]]] = {}
        for descriptor_domain, descriptor in descriptors.items():
            refs = [str(item).strip() for item in list(descriptor.get("rag_profiles") or []) if str(item).strip()]
            loaded_profiles: list[dict[str, Any]] = []
            seen_source_ids: set[str] = set()
            for profile_ref in refs:
                profile_path = self._resolve_ref(profile_ref)
                if not profile_path.exists():
                    raise ValueError(f"rag profile not found for {descriptor_domain}: {profile_ref}")
                payload = self._load_json(profile_path)
                errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
                if errors:
                    readable = "; ".join(
                        f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors
                    )
                    raise ValueError(f"invalid rag profile {profile_path}: {readable}")

                profile_domain = str(payload.get("domain_id") or "").strip()
                if profile_domain not in known_domains:
                    raise ValueError(f"rag profile references unknown domain_id: {profile_domain}")
                if profile_domain != descriptor_domain:
                    raise ValueError(
                        "rag profile domain mismatch for "
                        f"{profile_path}: expected {descriptor_domain}, got {profile_domain}"
                    )

                source_id = str(payload.get("source_id") or "").strip()
                if source_id in seen_source_ids:
                    raise ValueError(f"duplicate rag source_id in domain {descriptor_domain}: {source_id}")
                seen_source_ids.add(source_id)

                self._validate_indexing_config(payload, profile_path=profile_path)
                self._validate_allowed_paths(payload, profile_path=profile_path)
                self._validate_glob_list(payload, key="include_globs", profile_path=profile_path)
                self._validate_glob_list(payload, key="exclude_globs", profile_path=profile_path)
                loaded_profiles.append(dict(payload))
            profiles_by_domain[descriptor_domain] = loaded_profiles
        self._profiles_by_domain = profiles_by_domain
        return {
            domain_id: [dict(profile) for profile in profiles]
            for domain_id, profiles in self._profiles_by_domain.items()
        }

    def profiles_for_domain(self, domain_id: str) -> list[dict[str, Any]]:
        return [dict(profile) for profile in self._profiles_by_domain.get(str(domain_id).strip(), [])]

    def profiles_for_indexing(self, *, domain_id: str | None = None) -> list[dict[str, Any]]:
        profiles = self._collect_profiles(domain_id=domain_id)
        return [dict(profile) for profile in profiles if self._supports_indexing(profile)]

    def profiles_for_retrieval(
        self,
        domain_id: str,
        *,
        retrieval_intent: str,
        max_profiles: int = 8,
    ) -> list[dict[str, Any]]:
        intent_tokens = self._tokenize(str(retrieval_intent or ""))
        profiles = self._collect_profiles(domain_id=domain_id)
        if not intent_tokens:
            return [dict(profile) for profile in profiles[: max(1, max_profiles)]]

        ranked: list[tuple[float, dict[str, Any]]] = []
        for profile in profiles:
            usage_tokens = self._tokenize(str(profile.get("intended_usage") or ""))
            overlap = len(intent_tokens.intersection(usage_tokens))
            source_type = str(profile.get("source_type") or "")
            source_bonus = 0.25 if source_type in {"api_docs", "project_reference", "internal_docs"} else 0.0
            ranked.append((float(overlap) + source_bonus, profile))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("source_id") or "")))
        selected = [dict(profile) for _score, profile in ranked[: max(1, max_profiles)]]
        return selected

    def _collect_profiles(self, *, domain_id: str | None = None) -> list[dict[str, Any]]:
        if domain_id is not None:
            return [dict(profile) for profile in self._profiles_by_domain.get(str(domain_id).strip(), [])]
        collected: list[dict[str, Any]] = []
        for profiles in self._profiles_by_domain.values():
            collected.extend(dict(profile) for profile in profiles)
        return collected

    @staticmethod
    def _supports_indexing(profile: dict[str, Any]) -> bool:
        ingestion_path = str(profile.get("ingestion_path") or "").strip().lower()
        return ingestion_path in {"codecompass", "rag_helper", "codecompass/rag_helper"}

    @staticmethod
    def _tokenize(value: str) -> set[str]:
        return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", value or "") if len(token) > 2}

    @staticmethod
    def _validate_indexing_config(payload: dict[str, Any], *, profile_path: Path) -> None:
        indexing_config_ref = str(payload.get("indexing_config_ref") or "").strip()
        if not indexing_config_ref:
            raise ValueError(f"rag profile missing indexing config: {profile_path}")

    @staticmethod
    def _validate_allowed_paths(payload: dict[str, Any], *, profile_path: Path) -> None:
        allowed_paths = [str(item).strip() for item in list(payload.get("allowed_paths") or [])]
        for allowed_path in allowed_paths:
            if not allowed_path:
                raise ValueError(f"rag profile has empty allowed path: {profile_path}")
            candidate = Path(allowed_path)
            if candidate.is_absolute():
                raise ValueError(f"rag profile allowed path must be relative: {profile_path}")
            if ".." in candidate.parts:
                raise ValueError(f"rag profile allowed path traversal is not allowed: {profile_path}")

    @staticmethod
    def _validate_glob_list(payload: dict[str, Any], *, key: str, profile_path: Path) -> None:
        for pattern in [str(item).strip() for item in list(payload.get(key) or [])]:
            if not pattern:
                raise ValueError(f"rag profile has empty {key} pattern: {profile_path}")
            translated = fnmatch.translate(pattern)
            try:
                re.compile(translated)
            except re.error as exc:
                raise ValueError(f"rag profile has malformed glob in {key}: {pattern}") from exc

    def _resolve_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        return self.repository_root / ref

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
