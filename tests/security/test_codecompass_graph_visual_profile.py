"""Defense-in-depth source invariants for CodeCompass graph profiles.

The executable behavior of profile import, storage recovery, and hostile tooltip
labels is covered by the focused Angular Vitest specs and the real Playwright
functional gate.  This suite intentionally adds a separate release boundary:
dangerous DOM sinks or removal/reordering of the fail-closed checks must be
noticed even when a happy-path component test still passes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPH_FEATURE = (
    ROOT / "frontend-angular/src/app/features/codecompass-graph"
)
PROFILE_MODEL = GRAPH_FEATURE / "models/graph-visual-profile.model.ts"
PROFILE_VALIDATOR = (
    GRAPH_FEATURE / "services/graph-visual-profile-validator.ts"
)
PROFILE_FACADE = GRAPH_FEATURE / "services/graph-visual-profile.facade.ts"
LOCAL_STORAGE_ADAPTER = (
    GRAPH_FEATURE / "services/local-storage-graph-visual-profile.adapter.ts"
)
TOOLTIP_HELPER = (
    GRAPH_FEATURE / "components/graph-tooltip/graph-visual-tooltip.ts"
)
TWO_DIMENSIONAL_RENDERER = (
    GRAPH_FEATURE / "components/graph-2d-view/graph-2d-view.component.ts"
)
THREE_DIMENSIONAL_RENDERER = (
    GRAPH_FEATURE / "components/graph-3d-view/graph-3d-view.component.ts"
)
SHARED_THREE_DIMENSIONAL_RENDERER = (
    ROOT / "frontend-angular/src/app/features/graph-rendering/components/force-graph-3d-renderer.component.ts"
)


def _source(path: Path) -> str:
    assert path.is_file(), f"security source missing: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _assert_before(source: str, earlier: str, later: str) -> None:
    earlier_offset = source.find(earlier)
    later_offset = source.find(later)
    assert earlier_offset >= 0, f"missing security boundary: {earlier}"
    assert later_offset >= 0, f"missing guarded operation: {later}"
    assert earlier_offset < later_offset, (
        f"security boundary must precede guarded operation: {earlier!r} before {later!r}"
    )


def test_profile_validation_keeps_recursive_object_safety_as_the_first_boundary() -> None:
    source = _source(PROFILE_VALIDATOR)

    dangerous_keys = re.search(
        r"const DANGEROUS_KEYS = new Set\(\[([^]]+)]\);",
        source,
    )
    assert dangerous_keys is not None
    for key in ("__proto__", "prototype", "constructor"):
        assert f"'{key}'" in dangerous_keys.group(1)

    _assert_before(source, "this.validateObjectSafety(input", "if (!isRecord(input))")
    _assert_before(source, "this.validateObjectSafety(input", "this.toCanonicalProfile(input)")
    assert "Object.getPrototypeOf(value)" in source
    assert "prototype === Object.prototype || prototype === null" in source
    assert "ancestors.has(value)" in source
    assert "'cyclic_object'" in source
    assert "depth > GRAPH_VISUAL_PROFILE_MAX_DEPTH" in source
    assert "'max_depth_exceeded'" in source
    assert "for (const key of Object.keys(value))" in source
    assert "DANGEROUS_KEYS.has(key)" in source
    assert "this.validateObjectSafety(" in source


def test_profile_colors_and_visual_numbers_remain_strictly_bounded() -> None:
    model = _source(PROFILE_MODEL)
    validator = _source(PROFILE_VALIDATOR)

    assert "GRAPH_VISUAL_PROFILE_MAX_WEIGHT = 100" in model
    assert "GRAPH_VISUAL_PROFILE_MAX_DEPTH = 12" in model
    assert "GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES = 131_072" in model
    assert "const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;" in validator
    assert "!COLOR_PATTERN.test(color)" in validator
    assert "'invalid_color'" in validator
    assert (
        "this.validateRange(input['nodeSizeRange'], '$.nodeSizeRange', 1, 100, issues);"
        in validator
    )
    assert (
        "this.validateRange(input['edgeThicknessRange'], '$.edgeThicknessRange', 0.1, 20, issues);"
        in validator
    )
    assert "this.validateFiniteNumber(value[key], `${path}.${key}`, issues, 1, 5);" in validator
    assert "Number.isFinite(value)" in validator
    assert "value < min || value > max" in validator


def test_profile_import_rejects_oversized_bytes_before_decoding_blob_content() -> None:
    source = _source(PROFILE_FACADE)

    _assert_before(
        source,
        "if (file.size > GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES)",
        "return this.importProfile(await file.text())",
    )
    assert "new TextEncoder().encode(serialized).byteLength" in source
    assert source.count("'import_too_large'") >= 2
    assert "Number.isFinite(file.size)" in source
    assert "file.size < 0" in source


def test_tooltip_and_renderer_sources_have_no_executable_html_sink() -> None:
    forbidden_sinks = {
        "innerHTML": re.compile(r"\binnerHTML\b"),
        "outerHTML": re.compile(r"\bouterHTML\b"),
        "insertAdjacentHTML": re.compile(r"\binsertAdjacentHTML\b"),
        "Angular trust bypass": re.compile(r"\bbypassSecurityTrust\w*\b|\bDomSanitizer\b"),
        "document.write": re.compile(r"\bdocument\s*\.\s*write\s*\("),
        "eval": re.compile(r"(?:^|[^\w.])eval\s*\("),
        "Function constructor": re.compile(r"\bnew\s+Function\s*\("),
        "inline event attribute": re.compile(
            r"setAttribute\s*\(\s*['\"]on[a-z]+['\"]",
            re.IGNORECASE,
        ),
    }
    violations: list[str] = []
    for path in sorted(GRAPH_FEATURE.rglob("*.ts")):
        if path.name.endswith(".spec.ts"):
            continue
        source = _source(path)
        for label, pattern in forbidden_sinks.items():
            if pattern.search(source):
                violations.append(f"{path.relative_to(ROOT)}: {label}")

    assert violations == []

    tooltip = _source(TOOLTIP_HELPER)
    renderer_2d = _source(TWO_DIMENSIONAL_RENDERER)
    renderer_3d = _source(THREE_DIMENSIONAL_RENDERER)
    shared_renderer_3d = _source(SHARED_THREE_DIMENSIONAL_RENDERER)
    assert "tooltip.textContent = text" in tooltip
    assert "element.textContent = text" in renderer_2d
    assert renderer_3d.count("tooltip: graphVisualTooltipText(") >= 4
    assert "element.textContent = text" in shared_renderer_3d
    assert ".nodeLabel(" in shared_renderer_3d
    assert ".linkLabel(" in shared_renderer_3d


def test_local_storage_failures_and_corrupt_values_recover_to_the_default_profile() -> None:
    adapter = _source(LOCAL_STORAGE_ADAPTER)
    facade = _source(PROFILE_FACADE)

    for reason_code in (
        "storage_unavailable",
        "storage_read_failed",
        "storage_write_failed",
        "storage_quota_exceeded",
        "storage_remove_failed",
    ):
        assert f"'{reason_code}'" in adapter

    assert "this.storage.getItem" in adapter
    assert "this.storage.setItem" in adapter
    assert "this.storage.removeItem" in adapter
    assert adapter.count("try {") >= 4
    assert adapter.count("catch") >= 4
    assert "this.activeProfile.set(DEFAULT_GRAPH_VISUAL_PROFILE)" in facade
    assert "const parsed = this.parseImport(stored.value);" in facade
    _assert_before(
        facade,
        "if (!parsed.valid || !parsed.profile)",
        "this.activeProfile.set(parsed.profile)",
    )
    assert "catch {" in facade
    assert "'invalid_json'" in facade
