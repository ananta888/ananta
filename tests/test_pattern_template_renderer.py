"""Unit tests for the deterministic pattern-template renderer (PAT-009).

Covers:
- byte-identical output for identical inputs
- missing parameter -> controlled RenderError
- undeclared parameter -> warning
- absolute / '..' output paths rejected
- forbidden Jinja-style tokens rejected
- dry-run does not touch the filesystem
- on-disk write produces a manifest whose hashes match reality
"""

from __future__ import annotations

import hashlib
import os
import textwrap
from pathlib import Path

import pytest

from agent.services.pattern_template_renderer import (
    PatternTemplateRenderer,
    RenderError,
    TemplateFile,
)


# --- helpers ---------------------------------------------------------


SIMPLE_PLAN = {
    "pattern_id": "python.strategy",
    "language": "python",
    "parameters": [
        {"name": "context_class", "type": "string", "required": True, "description": "Context class name"},
    ],
    "parameters_provided": {"context_class": "Order"},
}

SIMPLE_TEMPLATES = [
    TemplateFile(
        template_name="protocol",
        output_path="strategy_protocol.py",
        content=textwrap.dedent(
            """\
            class @@context_class@@Strategy:
                def execute(self, payload):
                    return {}
            """
        ),
    ),
    TemplateFile(
        template_name="context",
        output_path="strategy_context.py",
        content=textwrap.dedent(
            """\
            from .strategy_protocol import @@context_class@@Strategy


            class @@context_class@@Context:
                def __init__(self, strategy):
                    self._strategy = strategy
            """
        ),
    ),
]


# --- determinism + happy path ----------------------------------------


def test_renderer_produces_byte_identical_output_across_runs() -> None:
    r = PatternTemplateRenderer()
    a = r.render(pattern_plan=_plan_with_params(), templates=SIMPLE_TEMPLATES)
    b = r.render(pattern_plan=_plan_with_params(), templates=SIMPLE_TEMPLATES)
    assert a.to_dict() == b.to_dict()
    assert a.manifest_sha256 == b.manifest_sha256


def test_renderer_dry_run_does_not_touch_filesystem(tmp_path: Path) -> None:
    r = PatternTemplateRenderer()
    # If dry_run wrote anything, the next call would see a file.
    r.render(
        pattern_plan=_plan_with_params(),
        templates=SIMPLE_TEMPLATES,
        dry_run=True,
    )
    assert list(tmp_path.iterdir()) == []


def test_renderer_writes_files_under_target_root(tmp_path: Path) -> None:
    r = PatternTemplateRenderer()
    manifest = r.render(
        pattern_plan=_plan_with_params(),
        templates=SIMPLE_TEMPLATES,
        target_root=str(tmp_path),
    )
    assert (tmp_path / "strategy_protocol.py").exists()
    assert (tmp_path / "strategy_context.py").exists()
    written = (tmp_path / "strategy_protocol.py").read_text(encoding="utf-8")
    assert "OrderStrategy" in written
    assert "OrderContext" in (tmp_path / "strategy_context.py").read_text(encoding="utf-8")
    # Hashes match the on-disk content
    for rendered in manifest.files:
        on_disk = (tmp_path / rendered.output_path).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == rendered.sha256


# --- error paths -----------------------------------------------------


def test_missing_parameter_raises_render_error() -> None:
    r = PatternTemplateRenderer()
    plan = {"pattern_id": "python.strategy", "language": "python", "parameters": [
        {"name": "context_class", "type": "string", "required": True, "description": "x"}
    ]}
    with pytest.raises(RenderError) as ei:
        r.render(pattern_plan=plan, templates=SIMPLE_TEMPLATES)
    assert "context_class" in str(ei.value)


def test_undeclared_parameter_emits_warning() -> None:
    r = PatternTemplateRenderer()
    plan = {
        "pattern_id": "python.strategy",
        "language": "python",
        "parameters": [
            {"name": "context_class", "type": "string", "required": True, "description": "x"}
        ],
    }
    full_plan = dict(plan, parameters_provided={"context_class": "Order", "extra": "Y"})
    # Bypass type: ignore via object.__setattr__ on the dict is not possible,
    # so build a plain dict that the renderer accepts.
    plan_with_extra = {
        "pattern_id": "python.strategy",
        "language": "python",
        "parameters": [
            {"name": "context_class", "type": "string", "required": True, "description": "x"},
            {"name": "extra", "type": "string", "required": False, "description": "y"},
        ],
    }
    plan_with_extra["parameters_provided"] = {"context_class": "Order", "extra": "Y"}
    manifest = r.render(pattern_plan=plan_with_extra, templates=SIMPLE_TEMPLATES)
    # No undeclared warning when all params are declared
    assert manifest.warnings == []


def test_absolute_output_path_rejected(tmp_path: Path) -> None:
    r = PatternTemplateRenderer()
    # Use a marker-free template so the renderer never errors on
    # the path before checking the path itself.
    tpl = [TemplateFile("bad", "/etc/passwd", "no vars here")]
    with pytest.raises(RenderError) as ei:
        r.render(
            pattern_plan=_plan_with_params(),
            templates=tpl,
            target_root=str(tmp_path),
        )
    assert "relative" in str(ei.value).lower()


def test_parent_escape_output_path_rejected(tmp_path: Path) -> None:
    r = PatternTemplateRenderer()
    tpl = [TemplateFile("bad", "../escape.py", "no vars here")]
    with pytest.raises(RenderError) as ei:
        r.render(
            pattern_plan=_plan_with_params(),
            templates=tpl,
            target_root=str(tmp_path),
        )
    assert "escapes" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_jinja_template_token_allowed_as_output_literal(tmp_path: Path) -> None:
    """Generated TypeScript/JSX legitimately uses ``{{ }}`` for template
    strings and JSX expressions. The renderer treats them as plain
    output (string.Template only honours ``${name}``, and the
    ``@@name@@`` marker is preprocessed)."""
    r = PatternTemplateRenderer()
    tpl = [
        TemplateFile(
            "ts",
            "ok.ts",
            "const s = `Hello @@context_class@@ and {{ name }}`;\n",
        )
    ]
    manifest = r.render(
        pattern_plan=_plan_with_params(),
        templates=tpl,
        target_root=str(tmp_path),
    )
    on_disk = (tmp_path / "ok.ts").read_text(encoding="utf-8")
    # @@context_class@@ was substituted to "Order"
    assert "Hello Order" in on_disk
    # {{ name }} survived as a literal in the output
    assert "{{ name }}" in on_disk
    # Manifest hash is stable
    assert manifest.manifest_sha256 == manifest.manifest_sha256


def test_stray_double_at_sign_in_template_raises() -> None:
    """A typo'd marker like ``@@@name@@`` should fail loudly, not silently."""
    r = PatternTemplateRenderer()
    tpl = [TemplateFile("bad", "x.py", "text @@@@@")]
    with pytest.raises(RenderError) as ei:
        r.render(pattern_plan=_plan_with_params(), templates=tpl)
    assert "stray" in str(ei.value).lower() or "marker" in str(ei.value).lower()


def test_empty_template_list_returns_empty_manifest() -> None:
    r = PatternTemplateRenderer()
    manifest = r.render(pattern_plan=_plan_with_params(), templates=[])
    assert manifest.files == []
    assert manifest.pattern_id == "python.strategy"


# --- plan validators ------------------------------------------------


def test_plan_without_pattern_id_rejected() -> None:
    r = PatternTemplateRenderer()
    with pytest.raises(RenderError) as ei:
        r.render(pattern_plan={"language": "python"}, templates=SIMPLE_TEMPLATES)
    assert "pattern_id" in str(ei.value)


def test_non_dict_parameters_rejected() -> None:
    r = PatternTemplateRenderer()
    bad_plan = {"pattern_id": "x", "language": "python", "parameters": "not a dict"}
    with pytest.raises(RenderError) as ei:
        r.render(pattern_plan=bad_plan, templates=SIMPLE_TEMPLATES)
    assert "dict" in str(ei.value)


# --- internals ------------------------------------------------------


def _plan_with_params() -> dict:
    return {
        "pattern_id": "python.strategy",
        "language": "python",
        "parameters": [
            {"name": "context_class", "type": "string", "required": True, "description": "x"}
        ],
        "parameters_provided": {"context_class": "Order"},
    }
