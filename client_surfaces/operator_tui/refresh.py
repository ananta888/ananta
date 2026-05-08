from __future__ import annotations

from client_surfaces.operator_tui.models import RefreshPolicy
from client_surfaces.operator_tui.sections import SECTIONS, get_section


def refresh_policy_for(section_id: str) -> RefreshPolicy:
    section = get_section(section_id)
    return RefreshPolicy(
        section_id=section.id,
        timeout_seconds=max(0.2, float(section.timeout_seconds)),
        refresh_interval_seconds=max(1.0, float(section.refresh_interval_seconds)),
        retry_attempts=1,
    )


def all_refresh_policies() -> tuple[RefreshPolicy, ...]:
    return tuple(refresh_policy_for(section.id) for section in SECTIONS)


def should_refresh(*, elapsed_seconds: float, policy: RefreshPolicy, force: bool = False) -> bool:
    if force:
        return True
    return float(elapsed_seconds) >= policy.refresh_interval_seconds
