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
        base_dir = Path(getattr(self.registry, "_base", settings.data_dir)).expanduser().resolve()
        self._bundle_root = base_dir / "sources" / "codecompass-bundles"
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

    def _license_policy_report(self, *, pack: dict[str, Any], selected_sources: list[dict[str, Any]]) -> dict[str, Any]:
        policy = dict(pack.get("license_policy") or {})
        allowed = {str(item).strip() for item in list(policy.get("allowed_licenses") or []) if str(item).strip()}
        require_license_ref = bool(policy.get("require_license_ref", True))
        block_on_missing_license = bool(policy.get("block_on_missing_license", False))
        warnings: list[str] = []
        blocking_errors: list[str] = []
        rows: list[dict[str, str]] = []
        for row in selected_sources:
            source_id = str(dict(row).get("source_id") or "").strip()
            descriptor = self.registry.get_source(source_id) or {}
            citation = dict(descriptor.get("citation_source") or {})
            row_citation = dict(dict(row).get("citation_source") or {})
            license_ref = str(citation.get("license_ref") or row_citation.get("license_ref") or "").strip()
            rows.append({"source_id": source_id, "license_ref": license_ref or "missing"})
            if not license_ref and require_license_ref:
                msg = f"missing_license_ref:{source_id}"
                if block_on_missing_license:
                    blocking_errors.append(msg)
                else:
                    warnings.append(msg)
                continue
            if license_ref and allowed and license_ref not in allowed:
                msg = f"license_not_allowed:{source_id}:{license_ref}"
                if block_on_missing_license:
                    blocking_errors.append(msg)
                else:
                    warnings.append(msg)
        return {
            "require_license_ref": require_license_ref,
            "block_on_missing_license": block_on_missing_license,
            "allowed_licenses": sorted(allowed),
            "rows": rows,
            "warnings": warnings,
            "blocking_errors": blocking_errors,
        }

    def _build_citation_bundle(self, *, source_ids: list[str]) -> dict[str, Any]:
        rows: list[dict[str, str]] = []
        for source_id in source_ids:
            descriptor = self.registry.get_source(source_id) or {}
            citation = dict(descriptor.get("citation_source") or {})
            rows.append(
                {
                    "source_id": source_id,
                    "canonical_url": str(citation.get("canonical_url") or ""),
                    "title": str(citation.get("title") or ""),
                    "publisher": str(citation.get("publisher") or ""),
                    "license_ref": str(citation.get("license_ref") or ""),
                }
            )
        return {
            "schema": "source_pack_citation_bundle.v1",
            "created_at": _now_iso(),
            "items": rows,
        }

    def _latest_bundle_for_pack(self, *, source_pack_id: str) -> dict[str, Any] | None:
        candidates = sorted(self._bundle_root.glob("ccb-*.json"), reverse=True)
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(dict(payload).get("source_pack_id") or "") != source_pack_id:
                continue
            row = dict(payload)
            row["bundle_path"] = str(path)
            return row
        return None

    def build_source_references(
        self,
        *,
        source_pack_id: str,
        query: str,
        include_wikipedia: bool = True,
        include_local_project: bool = True,
    ) -> dict[str, Any]:
        source_ids = self.resolve_retrieval_sources(
            source_pack_id=source_pack_id,
            query=query,
            include_wikipedia=include_wikipedia,
        )
        bundle = self._latest_bundle_for_pack(source_pack_id=source_pack_id) or {}
        bundle_id = str(bundle.get("bundle_id") or "")
        refs: list[dict[str, Any]] = []
        for source_id in source_ids:
            descriptor = self.registry.get_source(source_id) or {}
            latest = self.snapshots.latest_indexed_snapshot(source_id=source_id) or {}
            refs.append(
                {
                    "source_pack_id": source_pack_id,
                    "source_id": source_id,
                    "snapshot_id": str(latest.get("snapshot_id") or ""),
                    "trust_level": str(descriptor.get("trust_level") or ""),
                    "codecompass_bundle_id": bundle_id,
                }
            )
        if include_local_project:
            refs.insert(
                0,
                {
                    "source_pack_id": source_pack_id,
                    "source_id": "local-project-context",
                    "snapshot_id": "",
                    "trust_level": "local_project",
                    "codecompass_bundle_id": bundle_id,
                },
            )
        context_hash = _stable_hash(
            {
                "source_pack_id": source_pack_id,
                "query": query,
                "source_ids": [str(item.get("source_id") or "") for item in refs],
                "bundle_id": bundle_id,
            }
        )[:32]
        for row in refs:
            row["context_hash"] = context_hash
        return {
            "source_pack_id": source_pack_id,
            "query": query,
            "codecompass_bundle_id": bundle_id,
            "context_hash": context_hash,
            "source_references": refs,
        }

    def answer_preview(
        self,
        *,
        source_pack_id: str,
        query: str,
        include_wikipedia: bool = True,
        include_local_project: bool = True,
    ) -> dict[str, Any]:
        refs_payload = self.build_source_references(
            source_pack_id=source_pack_id,
            query=query,
            include_wikipedia=include_wikipedia,
            include_local_project=include_local_project,
        )
        refs = list(refs_payload.get("source_references") or [])
        origins: list[str] = []
        for row in refs:
            source_id = str(dict(row).get("source_id") or "")
            if source_id == "local-project-context":
                origins.append("local")
            elif source_id.startswith("eclipse-"):
                origins.append("eclipse")
            elif source_id.startswith("keycloak-"):
                origins.append("keycloak")
            elif source_id.startswith("wikimedia-"):
                origins.append("wikipedia")
            else:
                origins.append("other")
        unique_origins = sorted({item for item in origins if item})
        return {
            "status": "ok",
            "source_pack_id": source_pack_id,
            "query": query,
            "origins": unique_origins,
            "answer_text": f"Mock worker answer for query: {query}",
            **refs_payload,
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
            "cloud_context_policy": {
                "raw_content_included": False,
                "default_provider_access": "deny_raw_source_content",
                "allow_summary_context": True,
                "allowed_context_shapes": ["source_reference_summary", "citation_bundle"],
            },
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
        license_report = self._license_policy_report(pack=self.get_pack(source_pack_id), selected_sources=list(plan.get("selected_sources") or []))
        if dry_run:
            return {"status": "planned", **plan, "license_policy_report": license_report}
        if list(license_report.get("blocking_errors") or []):
            return {
                "status": "failed",
                "reason_code": "license_policy_blocked",
                "human_message": "License policy blocked bootstrap",
                **plan,
                "license_policy_report": license_report,
            }

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
        citation_bundle = self._build_citation_bundle(source_ids=[str(item.get("source_id") or "") for item in list(plan.get("selected_sources") or []) if str(item.get("source_id") or "").strip()])
        return {
            "status": "ok",
            **plan,
            "snapshot_ids": snapshot_ids,
            "codecompass_bundle": bundle,
            "citation_bundle": citation_bundle,
            "license_policy_report": license_report,
        }

    def doctor(self, *, source_pack_id: str = "ananta-dev-default") -> dict[str, Any]:
        pack = self.get_pack(source_pack_id)
        selected = self._selected_sources(pack=pack, skip_source_ids=set(), include_optional=False)
        required_ids = [str(item.get("source_id") or "") for item in selected if str(item.get("source_id") or "").strip()]
        by_source: dict[str, dict[str, Any]] = {}
        ready = True
        missing_steps: list[str] = []
        for source_id in required_ids:
            source = self.registry.get_source(source_id)
            latest_rows = self.snapshots.list_snapshots(source_id=source_id)
            latest = latest_rows[0] if latest_rows else None
            if source is None:
                ready = False
                missing_steps.append(f"register_source:{source_id}")
                by_source[source_id] = {"registered": False, "snapshot_status": "missing"}
                continue
            status = str(dict(latest or {}).get("status") or "missing")
            by_source[source_id] = {
                "registered": True,
                "snapshot_status": status,
                "trust_level": str(dict(source).get("trust_level") or ""),
                "license_ref": str(dict(dict(source).get("citation_source") or {}).get("license_ref") or ""),
            }
            if status != "indexed":
                ready = False
                missing_steps.append(f"refresh_or_bootstrap:{source_id}")
            if status in {"failed", "invalid", "blocked"}:
                ready = False
                missing_steps.append(f"repair_index:{source_id}")
        bundle_dir = self._bundle_root
        bundles = sorted(bundle_dir.glob("ccb-*.json"))
        bundle_ready = False
        for bundle_path in bundles:
            try:
                payload = json.loads(bundle_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(dict(payload).get("source_pack_id") or "") == source_pack_id:
                bundle_ready = True
                break
        if not bundle_ready:
            ready = False
            missing_steps.append(f"bootstrap_pack:{source_pack_id}")
        return {
            "schema": "source_pack_doctor.v1",
            "source_pack_id": source_pack_id,
            "ready": ready,
            "status": "ready" if ready else "not_ready",
            "required_sources": required_ids,
            "sources": by_source,
            "bundle_ready": bundle_ready,
            "bundle_count": len(bundles),
            "next_steps": sorted({step for step in missing_steps}),
        }
