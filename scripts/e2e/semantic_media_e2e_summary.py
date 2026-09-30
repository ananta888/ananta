"""Fail-closed summarisation of Playwright JSON reports into semantic-media gate measurements."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from agent.services.semantic_media_program_evidence import canonical_sha256


def _project_measurement_prefix(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized or "unknown"


def _summarize_report(raw: bytes) -> dict[str, int]:
    return _summarize_reports((("", raw),))


def _summarize_reports(
    raw_reports: Sequence[tuple[str, bytes]],
    *,
    failed_projects: Sequence[str] = (),
) -> dict[str, int]:
    reports: list[tuple[str, Mapping[str, Any]]] = []
    invalid_projects: list[str] = []
    for fallback_project, raw in raw_reports:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            invalid_projects.append(fallback_project or "unknown")
            continue
        report = _extract_playwright_json(decoded)
        if report is None:
            invalid_projects.append(fallback_project or "unknown")
            continue
        reports.append((fallback_project, report))

    statuses: list[tuple[str, str]] = []
    browsers: set[str] = set()
    browsers.update(project for project, _raw in raw_reports if project)
    peer_product_metrics: list[dict[str, int]] = []
    group_hub_authority_metrics: list[dict[str, int]] = []
    visual_lifecycle_metrics: list[dict[str, int]] = []
    seen_metric_annotations: set[tuple[str, str]] = set()

    def walk(value: Any, inherited_project: str = "", fixed_project: str = "") -> None:
        if isinstance(value, Mapping):
            project = value.get("projectName")
            if fixed_project:
                inherited_project = fixed_project
            elif isinstance(project, str) and project:
                browsers.add(project)
                inherited_project = project
            results = value.get("results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, Mapping) and isinstance(result.get("status"), str):
                        statuses.append((inherited_project or "unknown", str(result["status"])))
            annotations = value.get("annotations")
            if isinstance(annotations, list):
                for annotation in annotations:
                    if not isinstance(annotation, Mapping):
                        continue
                    try:
                        metrics = json.loads(str(annotation.get("description") or ""))
                    except json.JSONDecodeError:
                        continue
                    if annotation.get("type") == "semantic-peer-product-path-v2" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "browser_processes",
                            "browser_engines",
                            "product_facade_count",
                            "product_ui_projection_count",
                            "backend_route_count",
                            "p2p_product_delivery_count",
                            "forced_relay_delivery_count",
                            "duplicate_rejection_count",
                            "reorder_recovery_count",
                            "signed_preview_visible_count",
                            "comparison_preview_visible_count",
                            "reconnect_resume_count",
                            "revoke_ack_count",
                            "hub_curation_count",
                            "hub_receipt_verified_count",
                            "hub_dataset_reservation_count",
                            "live_revision_continuity_count",
                            "plaintext_canary_probe_count",
                            "synthetic_harness_count",
                            "partial_latency_ms",
                            "partial_observation_count",
                            "segment_mode_count",
                            "final_segment_count",
                            "correction_after_final_count",
                            "accelerated_rotation_count",
                            "voice_reconnect_count",
                            "observed_stream_404_count",
                            "observed_stop_409_count",
                            "observed_chunk_413_count",
                            "backpressure_count",
                            "ordinary_fallback_count",
                            "observed_error_response_count",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            peer_product_metrics.append(normalized)
                    if annotation.get("type") == "semantic-group-hub-authority-v2" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "participant_contexts",
                            "hub_admission_conflict_rejections",
                            "hub_admission_success_responses",
                            "hub_admission_denied_responses",
                            "hub_publication_generations",
                            "hub_published_track_count",
                            "hub_group_epoch_count",
                            "group_package_ack_count",
                            "membership_revoke_count",
                            "late_join_old_epoch_key_count",
                            "removed_member_new_epoch_key_count",
                            "independent_receiver_count",
                            "weak_receiver_fallback_count",
                            "browser_restart_recovery_count",
                            "ordinary_fallback_count",
                            "client_minted_sfu_token_count",
                            "client_generated_group_epoch_count",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            group_hub_authority_metrics.append(normalized)
                    if annotation.get("type") == "semantic-visual-lifecycle-v1" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "browser_processes",
                            "browser_engines",
                            "scenario_count",
                            "observe_count",
                            "active_count",
                            "recovery_count",
                            "revoke_count",
                            "reconnect_count",
                            "ordinary_fallback_count",
                            "direct_peer_links",
                            "ordinary_audio_receivers",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            visual_lifecycle_metrics.append(normalized)
            for nested in value.values():
                walk(nested, inherited_project, fixed_project)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, inherited_project, fixed_project)

    for fallback_project, report in reports:
        walk(report, fallback_project, fallback_project)

    passed = sum(status in {"passed", "expected"} for _project, status in statuses)
    skipped = sum(status == "skipped" for _project, status in statuses)
    reported_failed = len(statuses) - passed - skipped
    invalid_project_set = set(invalid_projects)
    normalized_failed_projects = {project or "unknown" for project in failed_projects}
    process_only_failures = {
        project
        for project in normalized_failed_projects
        if project not in invalid_project_set
        and not any(
            status not in {"passed", "expected", "skipped"}
            for status_project, status in statuses
            if status_project == project
        )
    }
    failed = reported_failed + len(invalid_projects) + len(process_only_failures)
    summary = {
        "executed_tests": len(statuses) - skipped,
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": skipped,
        "browser_count": len(browsers),
    }
    project_names = {
        *(project for project, _status in statuses),
        *invalid_projects,
        *normalized_failed_projects,
        *browsers,
    }
    failed_project_count = 0
    for project in sorted(project_names):
        project_statuses = [status for status_project, status in statuses if status_project == project]
        project_passed = sum(status in {"passed", "expected"} for status in project_statuses)
        project_skipped = sum(status == "skipped" for status in project_statuses)
        project_failed = (
            len(project_statuses)
            - project_passed
            - project_skipped
            + invalid_projects.count(project)
            + int(project in process_only_failures)
        )
        prefix = _project_measurement_prefix(project)
        summary.update(
            {
                f"{prefix}_executed_tests": len(project_statuses) - project_skipped,
                f"{prefix}_passed_tests": project_passed,
                f"{prefix}_failed_tests": project_failed,
                f"{prefix}_skipped_tests": project_skipped,
            }
        )
        failed_project_count += int(project_failed > 0 or project_skipped > 0)
    summary["failed_project_count"] = failed_project_count
    if peer_product_metrics:
        summed_fields = (
            "product_facade_count",
            "product_ui_projection_count",
            "backend_route_count",
            "p2p_product_delivery_count",
            "forced_relay_delivery_count",
            "duplicate_rejection_count",
            "reorder_recovery_count",
            "signed_preview_visible_count",
            "comparison_preview_visible_count",
            "reconnect_resume_count",
            "revoke_ack_count",
            "hub_curation_count",
            "hub_receipt_verified_count",
            "hub_dataset_reservation_count",
            "live_revision_continuity_count",
            "plaintext_canary_probe_count",
            "synthetic_harness_count",
            "partial_observation_count",
            "segment_mode_count",
            "final_segment_count",
            "correction_after_final_count",
            "accelerated_rotation_count",
            "voice_reconnect_count",
            "observed_stream_404_count",
            "observed_stop_409_count",
            "observed_chunk_413_count",
            "backpressure_count",
            "ordinary_fallback_count",
            "observed_error_response_count",
        )
        summary.update(
            {
                "peer_product_scenario_count": len(peer_product_metrics),
                "cross_engine_process_count": min(row["browser_processes"] for row in peer_product_metrics),
                "cross_engine_count": min(row["browser_engines"] for row in peer_product_metrics),
                "partial_latency_max_ms": max(row["partial_latency_ms"] for row in peer_product_metrics),
                **{field: sum(row[field] for row in peer_product_metrics) for field in summed_fields},
            }
        )
    if group_hub_authority_metrics:
        summed_fields = (
            "hub_admission_conflict_rejections",
            "hub_admission_success_responses",
            "hub_admission_denied_responses",
            "membership_revoke_count",
            "late_join_old_epoch_key_count",
            "removed_member_new_epoch_key_count",
            "weak_receiver_fallback_count",
            "browser_restart_recovery_count",
            "ordinary_fallback_count",
            "client_minted_sfu_token_count",
            "client_generated_group_epoch_count",
        )
        summary.update(
            {
                "group_hub_authority_scenario_count": len(group_hub_authority_metrics),
                "group_participant_context_min": min(
                    row["participant_contexts"] for row in group_hub_authority_metrics
                ),
                "hub_publication_generation_min": min(
                    row["hub_publication_generations"] for row in group_hub_authority_metrics
                ),
                "hub_published_track_min": min(row["hub_published_track_count"] for row in group_hub_authority_metrics),
                "hub_group_epoch_min": min(row["hub_group_epoch_count"] for row in group_hub_authority_metrics),
                "group_package_ack_min": min(row["group_package_ack_count"] for row in group_hub_authority_metrics),
                "independent_receiver_min": min(
                    row["independent_receiver_count"] for row in group_hub_authority_metrics
                ),
                **{
                    field.removesuffix("s") + "_count"
                    if field
                    in {
                        "hub_admission_conflict_rejections",
                        "hub_admission_success_responses",
                        "hub_admission_denied_responses",
                    }
                    else field: sum(row[field] for row in group_hub_authority_metrics)
                    for field in summed_fields
                },
            }
        )
    if visual_lifecycle_metrics:
        summary.update(
            {
                "visual_lifecycle_scenario_count": len(visual_lifecycle_metrics),
                "visual_process_count": min(row["browser_processes"] for row in visual_lifecycle_metrics),
                "visual_engine_count": min(row["browser_engines"] for row in visual_lifecycle_metrics),
                "visual_scenario_min": min(row["scenario_count"] for row in visual_lifecycle_metrics),
                "visual_observe_min": min(row["observe_count"] for row in visual_lifecycle_metrics),
                "visual_active_min": min(row["active_count"] for row in visual_lifecycle_metrics),
                "visual_recovery_min": min(row["recovery_count"] for row in visual_lifecycle_metrics),
                "visual_revoke_min": min(row["revoke_count"] for row in visual_lifecycle_metrics),
                "visual_reconnect_min": min(row["reconnect_count"] for row in visual_lifecycle_metrics),
                "visual_ordinary_fallback_min": min(row["ordinary_fallback_count"] for row in visual_lifecycle_metrics),
                "visual_direct_link_min": min(row["direct_peer_links"] for row in visual_lifecycle_metrics),
                "visual_ordinary_receiver_min": min(
                    row["ordinary_audio_receivers"] for row in visual_lifecycle_metrics
                ),
            }
        )
    return summary


def _extract_playwright_json(value: str) -> Mapping[str, Any] | None:
    """Recover the JSON reporter document after inherited service logs."""

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, Mapping) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[Mapping[str, Any]] = []
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping) and (
            isinstance(parsed.get("suites"), list)
            or isinstance(parsed.get("stats"), Mapping)
            or isinstance(parsed.get("config"), Mapping)
        ):
            candidates.append(parsed)
    return candidates[-1] if candidates else None
