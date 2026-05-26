from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class SourcePackService:
    def __init__(
        self,
        *,
        registry: SourceRegistry | None = None,
        snapshots: SourceSnapshotStore | None = None,
    ) -> None:
        self.registry = registry or SourceRegistry()
        self.snapshots = snapshots or SourceSnapshotStore()
        self._bundle_root = Path(settings.data_dir).expanduser().resolve() / "sources" / "codecompass-bundles"
        self._bundle_root.mkdir(parents=True, exist_ok=True)

    def list_packs(self) -> list[dict[str, Any]]:
        return self.registry.list_source_packs()

    def get_pack(self, source_pack_id: str) -> dict[str, Any]:
        pack = self.registry.get_source_pack(source_pack_id)
        if pack is None:
            raise ValueError("source_pack_not_found")
        return pack

    def _selected_sources(self, *, pack: dict[str, Any], skip_source_ids: set[str], include_optional: bool) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for row in [dict(item) for item in list(pack.get("sources") or []) if isinstance(item, dict)]:
            source_id = str(row.get("source_id") or "").strip()
            if not source_id or source_id in skip_source_ids:
                continue
            if bool(row.get("optional", False)) and not include_optional:
                continue
            selected.append(row)
        return selected

    def resolve_retrieval_sources(self, *, source_pack_id: str, query: str, include_wikipedia: bool = False) -> list[str]:
        pack = self.get_pack(source_pack_id)
        sources = self._selected_sources(pack=pack, skip_source_ids=set(), include_optional=False)
        ids = [str(item.get("source_id") or "") for item in sources if str(item.get("source_id") or "").strip()]
        ranked = self.registry.rank_sources_for_query(source_pack_id=source_pack_id, source_ids=ids, query=query)
        lowered = str(query or "").lower()
        mode = "general_fallback"
        if any(token in lowered for token in ("eclipse", "plugin.xml", "manifest.mf", "osgi", "pde", "jdt")):
            mode = "technical_eclipse"
        elif any(token in lowered for token in ("keycloak", "oidc", "realm", "token", "client")):
            mode = "technical_keycloak"

        rule = next(
            (
                dict(item)
                for item in list(pack.get("retrieval_rules") or [])
                if isinstance(item, dict) and str(item.get("match_mode") or "") == mode
            ),
            {},
        )
        include_ids = {str(item).strip() for item in list(rule.get("include_source_ids") or []) if str(item).strip()}
        exclude_ids = {str(item).strip() for item in list(rule.get("exclude_source_ids") or []) if str(item).strip()}

        filtered = [sid for sid in ranked if (not include_ids or sid in include_ids) and sid not in exclude_ids]
        if include_wikipedia:
            return filtered
        return [sid for sid in filtered if sid != "wikimedia-wikipedia-initial-dump"]

    def plan_bootstrap(
        self,
        *,
        source_pack_id: str,
        dry_run: bool = False,
        skip_source_ids: list[str] | None = None,
        include_optional: bool = False,
    ) -> dict[str, Any]:
        pack = self.get_pack(source_pack_id)
        skip_set = {str(item).strip() for item in list(skip_source_ids or []) if str(item).strip()}
        selected = self._selected_sources(pack=pack, skip_source_ids=skip_set, include_optional=include_optional)
        warnings: list[str] = []
        if any(str(item.get("source_id") or "") == "wikimedia-wikipedia-initial-dump" for item in selected):
            warnings.append("wikipedia_dump_large_download_warning")
        return {
            "source_pack_id": source_pack_id,
            "display_name": str(pack.get("display_name") or ""),
            "dry_run": bool(dry_run),
            "selected_sources": selected,
            "skip_source_ids": sorted(skip_set),
            "warnings": warnings,
            "bootstrap_steps": list(pack.get("bootstrap_steps") or []),
        }

    def _create_fixture_snapshot(self, *, source_id: str, descriptor_hash: str) -> dict[str, Any]:
        snapshot = self.snapshots.build_snapshot(
            source_id=source_id,
            descriptor_hash=descriptor_hash or ("0" * 64),
            content_payload=[{"source_id": source_id, "mode": "source_pack_bootstrap_fixture"}],
            metadata_payload={"bootstrap_mode": "fixture", "source_id": source_id},
            status="indexed",
            reason_code="",
            human_message="source_pack_bootstrap_fixture",
            retrieved_at=_now_iso(),
        )
        return self.snapshots.save_snapshot(snapshot)

    def _build_index_profile(self) -> dict[str, Any]:
        return {
            "schema": "eclipse_codecompass_index_profile.v1",
            "include_patterns": ["**/plugin.xml", "**/MANIFEST.MF", "**/*.java"],
            "extractors": ["osgi_manifest", "plugin_xml_extensions", "java_package_hierarchy"],
            "notes": "Metadata-only profile; no raw source content stored in bundle.",
        }

    def _write_bundle(self, *, source_pack_id: str, snapshot_ids: list[str], profile_refs: list[str]) -> dict[str, Any]:
        payload = {
            "schema": "codecompass_bundle.v1",
            "source_pack_id": source_pack_id,
            "source_snapshot_ids": snapshot_ids,
            "profile_refs": profile_refs,
            "created_at": _now_iso(),
            "index_hash": _stable_hash({"source_pack_id": source_pack_id, "source_snapshot_ids": snapshot_ids, "profile_refs": profile_refs}),
            "metadata_only": True,
            "index_profile": self._build_index_profile(),
        }
        bundle_id = f"ccb-{hashlib.sha1(payload['index_hash'].encode('utf-8')).hexdigest()[:14]}"
        payload["bundle_id"] = bundle_id
        bundle_path = self._bundle_root / f"{bundle_id}.json"
        bundle_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**payload, "bundle_path": str(bundle_path)}

    def bootstrap(
        self,
        *,
        source_pack_id: str,
        dry_run: bool = False,
        skip_source_ids: list[str] | None = None,
        include_optional: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan_bootstrap(
            source_pack_id=source_pack_id,
            dry_run=dry_run,
            skip_source_ids=skip_source_ids,
            include_optional=include_optional,
        )
        if dry_run:
            return {"status": "planned", **plan}

        self.registry.register_source_pack_with_options(
            source_pack_id=source_pack_id,
            overwrite_existing=True,
            include_optional=include_optional,
        )

        snapshot_ids: list[str] = []
        for row in list(plan.get("selected_sources") or []):
            source_id = str(dict(row).get("source_id") or "").strip()
            if not source_id:
                continue
            descriptor = self.registry.get_source(source_id)
            descriptor_hash = str(dict(descriptor or {}).get("extensions", {}).get("descriptor_hash") or "")
            snap = self._create_fixture_snapshot(source_id=source_id, descriptor_hash=descriptor_hash)
            snapshot_ids.append(str(snap.get("snapshot_id") or ""))

        profile_refs = [
            str(item.get("profile_id") or "")
            for item in list(self.get_pack(source_pack_id).get("codecompass_profiles") or [])
            if isinstance(item, dict) and str(item.get("profile_id") or "").strip()
        ]
        bundle = self._write_bundle(source_pack_id=source_pack_id, snapshot_ids=snapshot_ids, profile_refs=profile_refs)
        return {
            "status": "ok",
            **plan,
            "snapshot_ids": snapshot_ids,
            "codecompass_bundle": bundle,
        }
