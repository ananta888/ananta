from __future__ import annotations

from urllib.parse import urlparse

from flask import current_app

from agent.services.repository_registry import get_repository_registry


def _display_agent_name(*, url: str | None, agent_name: str | None = None) -> str | None:
    name = str(agent_name or "").strip()
    if name:
        return name
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        return parsed.hostname or raw
    except Exception:
        return raw


def _normalize_artifact_flow_config(raw: dict | None) -> dict:
    payload = dict(raw or {})
    try:
        rag_top_k = int(payload.get("rag_top_k", 3))
    except (TypeError, ValueError):
        rag_top_k = 3
    rag_top_k = max(1, min(20, rag_top_k))
    try:
        max_tasks = int(payload.get("max_tasks", 30))
    except (TypeError, ValueError):
        max_tasks = 30
    max_tasks = max(1, min(200, max_tasks))
    try:
        max_worker_jobs = int(payload.get("max_worker_jobs_per_task", 5))
    except (TypeError, ValueError):
        max_worker_jobs = 5
    max_worker_jobs = max(1, min(20, max_worker_jobs))
    return {
        "enabled": bool(payload.get("enabled", True)),
        "rag_enabled": bool(payload.get("rag_enabled", True)),
        "rag_top_k": rag_top_k,
        "rag_include_content": bool(payload.get("rag_include_content", False)),
        "max_tasks": max_tasks,
        "max_worker_jobs_per_task": max_worker_jobs,
    }


def _extract_artifact_ids_from_chunks(chunks: list[dict] | None) -> list[str]:
    seen: set[str] = set()
    for chunk in list(chunks or []):
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        for key in ("artifact_id",):
            value = str(chunk.get(key) or metadata.get(key) or "").strip()
            if value:
                seen.add(value)
        for key in ("artifact_ids",):
            values = chunk.get(key)
            if not isinstance(values, list):
                values = metadata.get(key)
            if isinstance(values, list):
                for item in values:
                    value = str(item or "").strip()
                    if value:
                        seen.add(value)
    return sorted(seen)


def _extract_artifact_ids_from_refs(refs: list[dict] | None) -> list[str]:
    seen: set[str] = set()
    for ref in list(refs or []):
        if not isinstance(ref, dict):
            continue
        value = str(ref.get("artifact_id") or "").strip()
        if value:
            seen.add(value)
        values = ref.get("artifact_ids")
        if isinstance(values, list):
            for item in values:
                nested = str(item or "").strip()
                if nested:
                    seen.add(nested)
    return sorted(seen)


def _artifact_summary(artifact_id: str, *, repos) -> dict:
    value = str(artifact_id or "").strip()
    if not value:
        return {}
    artifact = repos.artifact_repo.get_by_id(value)
    if artifact is None:
        return {"artifact_id": value, "status": "missing"}
    links = repos.knowledge_link_repo.get_by_artifact(value)
    collection_names: list[str] = []
    for link in links:
        metadata = getattr(link, "link_metadata", None) or {}
        collection_name = str(metadata.get("collection_name") or "").strip()
        if not collection_name and getattr(link, "collection_id", None):
            collection = repos.knowledge_collection_repo.get_by_id(str(link.collection_id))
            collection_name = str(getattr(collection, "name", "") or "").strip()
        if collection_name and collection_name not in collection_names:
            collection_names.append(collection_name)
    documents = repos.extracted_document_repo.get_by_artifact(value)
    knowledge_index = repos.knowledge_index_repo.get_by_artifact(value)
    return {
        "artifact_id": artifact.id,
        "filename": artifact.latest_filename,
        "media_type": artifact.latest_media_type,
        "status": artifact.status,
        "size_bytes": artifact.size_bytes,
        "created_by": artifact.created_by,
        "collection_names": collection_names,
        "extracted_document_count": len(documents),
        "knowledge_index_status": getattr(knowledge_index, "status", None) if knowledge_index else None,
    }


def _artifact_summaries(artifact_ids: list[str], *, repos) -> list[dict]:
    seen: set[str] = set()
    summaries: list[dict] = []
    for artifact_id in artifact_ids:
        value = str(artifact_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        summary = _artifact_summary(value, repos=repos)
        if summary:
            summaries.append(summary)
    return summaries


def _resolve_assignment_summary(*, task: dict, repos, worker_url: str | None = None) -> dict:
    assigned_agent_url = str(task.get("assigned_agent_url") or worker_url or "").strip() or None
    assigned_role_id = str(task.get("assigned_role_id") or "").strip() or None
    team_id = str(task.get("team_id") or "").strip() or None

    agent = repos.agent_repo.get_by_url(assigned_agent_url) if assigned_agent_url else None
    member = None
    if team_id and assigned_agent_url:
        for candidate in repos.team_member_repo.get_by_team(team_id):
            if str(getattr(candidate, "agent_url", "") or "").strip() != assigned_agent_url:
                continue
            candidate_role_id = str(getattr(candidate, "role_id", "") or "").strip() or None
            if assigned_role_id and candidate_role_id != assigned_role_id:
                continue
            member = candidate
            break
        if member is None and not assigned_role_id:
            for candidate in repos.team_member_repo.get_by_team(team_id):
                if str(getattr(candidate, "agent_url", "") or "").strip() == assigned_agent_url:
                    member = candidate
                    break

    if not assigned_role_id and member is not None:
        assigned_role_id = str(getattr(member, "role_id", "") or "").strip() or None
    role = repos.role_repo.get_by_id(assigned_role_id) if assigned_role_id else None

    template_id = str(getattr(member, "custom_template_id", "") or "").strip() or None if member is not None else None
    if not template_id and role is not None:
        template_id = str(getattr(role, "default_template_id", "") or "").strip() or None
    template = repos.template_repo.get_by_id(template_id) if template_id else None

    return {
        "agent_url": assigned_agent_url,
        "agent_name": _display_agent_name(url=assigned_agent_url, agent_name=getattr(agent, "name", None)),
        "role_id": assigned_role_id,
        "role_name": getattr(role, "name", None) if role is not None else None,
        "template_id": template_id,
        "template_name": getattr(template, "name", None) if template is not None else None,
    }


def _accumulate_group_artifacts(group: dict, artifacts: list[dict]) -> None:
    existing = group.setdefault("artifacts", [])
    known_ids = {str(item.get("artifact_id") or "").strip() for item in existing if isinstance(item, dict)}
    for artifact in artifacts:
        artifact_id = str((artifact or {}).get("artifact_id") or "").strip()
        if not artifact_id or artifact_id in known_ids:
            continue
        existing.append(artifact)
        known_ids.add(artifact_id)


def build_artifact_flow_read_model(*, overrides: dict | None = None) -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    configured = _normalize_artifact_flow_config((cfg or {}).get("artifact_flow"))
    effective = _normalize_artifact_flow_config({**configured, **dict(overrides or {})})
    payload: dict = {
        "enabled": bool(effective.get("enabled", True)),
        "config": effective,
        "items": [],
        "counts": {"tasks": 0, "worker_jobs": 0, "worker_results": 0, "memory_entries": 0},
        "groups": {"by_worker": [], "by_assignment": []},
    }
    if not effective["enabled"]:
        return payload

    repos = get_repository_registry()
    tasks = [task.model_dump() for task in repos.task_repo.get_all()]
    sorted_tasks = sorted(
        tasks,
        key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
        reverse=True,
    )[: int(effective["max_tasks"])]
    payload["counts"]["tasks"] = len(sorted_tasks)
    rag_service = None
    if effective["rag_enabled"]:
        try:
            from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service

            rag_service = get_knowledge_index_retrieval_service()
        except Exception:
            rag_service = None

    worker_groups: dict[str, dict] = {}
    assignment_groups: dict[str, dict] = {}

    for task in sorted_tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        worker_jobs = repos.worker_job_repo.get_by_parent_task(task_id)[: int(effective["max_worker_jobs_per_task"])]
        payload["counts"]["worker_jobs"] += len(worker_jobs)
        task_memory_entries = repos.memory_entry_repo.get_by_task(task_id)
        payload["counts"]["memory_entries"] += len(task_memory_entries)

        task_bundle_id = str(task.get("context_bundle_id") or "").strip() or None
        task_bundle = repos.context_bundle_repo.get_by_id(task_bundle_id) if task_bundle_id else None
        sent_artifact_ids = _extract_artifact_ids_from_chunks((task_bundle.chunks if task_bundle else None) or [])
        assignment_summary = _resolve_assignment_summary(task=task, repos=repos)

        flow_jobs: list[dict] = []
        aggregate_returned_artifact_ids: set[str] = set()
        for job in worker_jobs:
            bundle = repos.context_bundle_repo.get_by_id(job.context_bundle_id) if getattr(job, "context_bundle_id", None) else None
            job_sent_artifact_ids = _extract_artifact_ids_from_chunks((bundle.chunks if bundle else None) or [])
            for artifact_id in job_sent_artifact_ids:
                sent_artifact_ids.append(artifact_id)

            result_rows = repos.worker_result_repo.get_by_worker_job(job.id)
            payload["counts"]["worker_results"] += len(result_rows)
            memory_rows = [entry for entry in task_memory_entries if str(entry.worker_job_id or "").strip() == str(job.id)]
            returned_refs: list[dict] = []
            for entry in memory_rows:
                refs = list(entry.artifact_refs or [])
                if refs:
                    returned_refs.extend(refs)
            returned_artifact_ids = _extract_artifact_ids_from_refs(returned_refs)
            aggregate_returned_artifact_ids.update(returned_artifact_ids)
            job_assignment = _resolve_assignment_summary(task=task, repos=repos, worker_url=job.worker_url)
            job_sent_artifacts = _artifact_summaries(job_sent_artifact_ids, repos=repos)
            job_returned_artifacts = _artifact_summaries(returned_artifact_ids, repos=repos)

            latest_result = result_rows[0] if result_rows else None
            flow_jobs.append(
                {
                    "worker_job_id": job.id,
                    "subtask_id": getattr(job, "subtask_id", None),
                    "status": job.status,
                    "worker_url": job.worker_url,
                    "worker_name": _display_agent_name(url=job.worker_url),
                    "assignment": job_assignment,
                    "context_bundle_id": job.context_bundle_id,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "sent_artifact_ids": job_sent_artifact_ids,
                    "sent_artifacts": job_sent_artifacts,
                    "returned_artifact_ids": returned_artifact_ids,
                    "returned_artifacts": job_returned_artifacts,
                    "returned_refs": [
                        {
                            **dict(ref or {}),
                            "artifact": _artifact_summary(str((ref or {}).get("artifact_id") or "").strip(), repos=repos)
                            if str((ref or {}).get("artifact_id") or "").strip()
                            else None,
                        }
                        for ref in returned_refs
                        if isinstance(ref, dict)
                    ],
                    "result_count": len(result_rows),
                    "latest_result_status": latest_result.status if latest_result else None,
                }
            )
            _touch_worker_group(
                worker_groups, worker_url=job.worker_url,
                worker_name=_display_agent_name(url=job.worker_url),
                task_id=task_id,
                worker_job_id=job.id,
                artifacts=[*job_sent_artifacts, *job_returned_artifacts],
                assignment=job_assignment,
            )
            _touch_assignment_group(
                assignment_groups, assignment=job_assignment,
                task_id=task_id,
                worker_job_id=job.id,
                artifacts=[*job_sent_artifacts, *job_returned_artifacts],
            )

        all_sent = sorted({item for item in sent_artifact_ids if str(item).strip()})
        sent_artifacts = _artifact_summaries(all_sent, repos=repos)
        returned_artifacts = _artifact_summaries(sorted(aggregate_returned_artifact_ids), repos=repos)
        rag_context: list[dict] = []
        if rag_service is not None and (all_sent or aggregate_returned_artifact_ids):
            query = " ".join(
                [
                    str(task.get("title") or "").strip(),
                    str(task.get("description") or "").strip(),
                ]
            ).strip()
            if query:
                try:
                    rag_rows = rag_service.search(
                        query,
                        top_k=int(effective["rag_top_k"]),
                        artifact_ids=set(all_sent) | set(aggregate_returned_artifact_ids),
                    )
                    for row in rag_rows:
                        item = {
                            "source": row.source,
                            "score": row.score,
                            "metadata": dict(row.metadata or {}),
                        }
                        if effective["rag_include_content"]:
                            item["content"] = row.content
                        rag_context.append(item)
                except Exception as exc:
                    current_app.logger.warning("artifact_flow rag enrichment failed for task %s: %s", task_id, exc)

        payload["items"].append(
            {
                "task_id": task_id,
                "title": task.get("title"),
                "status": task.get("status"),
                "assignment": assignment_summary,
                "updated_at": task.get("updated_at") or task.get("created_at"),
                "current_worker_job_id": task.get("current_worker_job_id"),
                "context_bundle_id": task_bundle_id,
                "sent_artifact_ids": all_sent,
                "sent_artifacts": sent_artifacts,
                "returned_artifact_ids": sorted(aggregate_returned_artifact_ids),
                "returned_artifacts": returned_artifacts,
                "worker_jobs": flow_jobs,
                "rag_context": rag_context,
            }
        )
        _touch_assignment_group(
            assignment_groups,
            assignment=assignment_summary,
            task_id=task_id,
            worker_job_id=str(task.get("current_worker_job_id") or "").strip() or None,
            artifacts=[*sent_artifacts, *returned_artifacts],
        )
        _touch_worker_group(
            worker_groups,
            worker_url=str(task.get("assigned_agent_url") or "").strip() or None,
            worker_name=assignment_summary.get("agent_name"),
            task_id=task_id,
            worker_job_id=str(task.get("current_worker_job_id") or "").strip() or None,
            artifacts=[*sent_artifacts, *returned_artifacts],
            assignment=assignment_summary,
        )

    payload["groups"] = {
        "by_worker": sorted(
            (
                {
                    **group,
                    "artifact_ids": [str(item.get("artifact_id") or "").strip() for item in group.get("artifacts") or [] if str(item.get("artifact_id") or "").strip()],
                }
                for group in worker_groups.values()
            ),
            key=lambda group: (-(len(group.get("artifacts") or [])), str(group.get("worker_name") or group.get("worker_url") or "")),
        ),
        "by_assignment": sorted(
            (
                {
                    **group,
                    "artifact_ids": [str(item.get("artifact_id") or "").strip() for item in group.get("artifacts") or [] if str(item.get("artifact_id") or "").strip()],
                }
                for group in assignment_groups.values()
            ),
            key=lambda group: (
                -(len(group.get("artifacts") or [])),
                str(group.get("agent_name") or group.get("agent_url") or ""),
                str(group.get("role_name") or ""),
                str(group.get("template_name") or ""),
            ),
        ),
    }
    return payload


def _touch_worker_group(worker_groups: dict, *, worker_url: str | None, worker_name: str | None, task_id: str, worker_job_id: str | None, artifacts: list[dict], assignment: dict | None) -> None:
    key = str(worker_url or "").strip()
    if not key:
        return
    group = worker_groups.setdefault(
        key,
        {
            "worker_url": key,
            "worker_name": _display_agent_name(url=key, agent_name=worker_name),
            "task_ids": [],
            "worker_job_ids": [],
            "role_names": [],
            "template_names": [],
            "artifacts": [],
        },
    )
    if task_id and task_id not in group["task_ids"]:
        group["task_ids"].append(task_id)
    if worker_job_id and worker_job_id not in group["worker_job_ids"]:
        group["worker_job_ids"].append(worker_job_id)
    role_name = str((assignment or {}).get("role_name") or "").strip()
    if role_name and role_name not in group["role_names"]:
        group["role_names"].append(role_name)
    template_name = str((assignment or {}).get("template_name") or "").strip()
    if template_name and template_name not in group["template_names"]:
        group["template_names"].append(template_name)
    _accumulate_group_artifacts(group, artifacts)


def _touch_assignment_group(assignment_groups: dict, *, assignment: dict | None, task_id: str, worker_job_id: str | None, artifacts: list[dict]) -> None:
    payload_assignment = dict(assignment or {})
    key = "::".join(
        [
            str(payload_assignment.get("agent_url") or "").strip(),
            str(payload_assignment.get("role_id") or "").strip(),
            str(payload_assignment.get("template_id") or "").strip(),
        ]
    )
    if not key.replace(":", "").strip():
        return
    group = assignment_groups.setdefault(
        key,
        {
            "agent_url": payload_assignment.get("agent_url"),
            "agent_name": payload_assignment.get("agent_name"),
            "role_id": payload_assignment.get("role_id"),
            "role_name": payload_assignment.get("role_name"),
            "template_id": payload_assignment.get("template_id"),
            "template_name": payload_assignment.get("template_name"),
            "task_ids": [],
            "worker_job_ids": [],
            "artifacts": [],
        },
    )
    if task_id and task_id not in group["task_ids"]:
        group["task_ids"].append(task_id)
    if worker_job_id and worker_job_id not in group["worker_job_ids"]:
        group["worker_job_ids"].append(worker_job_id)
    _accumulate_group_artifacts(group, artifacts)
