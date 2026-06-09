"""Integration test: render the seeded Strategy templates end-to-end.

This test reads the actual .tmpl files from
config/patterns/templates/python/strategy/, renders them through the
PatternTemplateRenderer, and asserts the generated Python:

- Parses via ast.parse
- Compiles via py_compile
- The rendered primary strategy actually returns the expected
  sorted+lowercased output at runtime.

It also exercises a Java strategy render to assert structure
(imports, class declarations) without requiring a JDK on the host.
"""

from __future__ import annotations

import ast
import hashlib
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agent.services.pattern_template_renderer import (
    PatternTemplateRenderer,
    TemplateFile,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PY_TMPL_DIR = REPO_ROOT / "config" / "patterns" / "templates" / "python" / "strategy"
JAVA_TMPL_DIR = REPO_ROOT / "config" / "patterns" / "templates" / "java" / "strategy"
TS_TMPL_DIR = REPO_ROOT / "config" / "patterns" / "templates" / "typescript" / "strategy"


def _load_templates(d: Path) -> list[TemplateFile]:
    out: list[TemplateFile] = []
    for path in sorted(d.glob("*.tmpl")):
        out.append(
            TemplateFile(
                template_name=path.stem,
                output_path=path.name[: -len(".tmpl")],
                content=path.read_text(encoding="utf-8"),
            )
        )
    return out


PLAN = {
    "pattern_id": "python.strategy",
    "language": "python",
    "parameters": [
        {"name": "context_class", "type": "string", "required": True, "description": "x"},
        {"name": "package_name", "type": "string", "required": True, "description": "x"},
        {"name": "pattern_id", "type": "string", "required": True, "description": "x"},
    ],
    "parameters_provided": {
        "context_class": "Order",
        "package_name": "demo.strategy",
        "pattern_id": "python.strategy",
    },
}


# --- Python strategy --------------------------------------------------


def test_python_strategy_templates_render_and_compile(tmp_path: Path) -> None:
    if not PY_TMPL_DIR.exists():
        pytest.skip("Python strategy templates not present in this checkout")
    r = PatternTemplateRenderer()
    templates = _load_templates(PY_TMPL_DIR)
    assert templates, "expected at least one python strategy .tmpl file"

    manifest = r.render(pattern_plan=PLAN, templates=templates, target_root=str(tmp_path))
    rendered_files = list(tmp_path.iterdir())
    assert rendered_files, "renderer produced no files"

    # Every generated .py file must compile and parse. We rewrite
    # `from .name import` to `from name import` so the loader can
    # import each file as a top-level module without a package
    # context (the templates are designed to live in a real
    # project package; the rewrite here is purely for the smoke
    # import below).
    rendered_py = []
    for f in rendered_files:
        if not f.name.endswith(".py"):
            continue
        original = f.read_text(encoding="utf-8")
        rewritten_py = original.replace("from .", "from ")
        f.write_text(rewritten_py, encoding="utf-8")
        rendered_py.append(f)
        py_compile.compile(str(f), doraise=True)
        tree = ast.parse(f.read_text(encoding="utf-8"))
        # Sanity: at least one top-level class or function.
        assert any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            for node in tree.body
        )

    # Smoke-import each .py file in isolation, then exec the
    # primary/secondary classes to assert their runtime behaviour
    # matches the expected deterministic contract.
    primary = tmp_path / "strategy_primary.py"
    secondary = tmp_path / "strategy_secondary.py"
    protocol = tmp_path / "strategy_protocol.py"
    context = tmp_path / "strategy_context.py"

    for f in (primary, secondary, protocol, context):
        assert f.exists(), f"missing rendered file: {f.name}"

    # strategy_protocol is a Protocol only — no runtime check needed.
    # strategy_primary / strategy_secondary are the swappable
    # strategies; strategy_context is the owner. The relative
    # imports have already been rewritten above, so we can just
    # import them as top-level modules.
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util as iu, sys; "
            f"sys.path.insert(0, {str(tmp_path)!r}); "
            "from strategy_primary import PrimaryOrderStrategy\n"
            "from strategy_secondary import SecondaryOrderStrategy\n"
            "from strategy_context import OrderContext\n"
            "ctx = OrderContext(PrimaryOrderStrategy())\n"
            "assert ctx.run({'items': ['B', 'a', 'c']}) == {'variant': 'primary', 'items': ['a', 'b', 'c']}, ctx.run({'items': ['B', 'a', 'c']})\n"
            "ctx.set_strategy(SecondaryOrderStrategy())\n"
            "assert ctx.run({'items': ['B', 'a', 'c']}) == {'variant': 'secondary', 'items': ['c', 'b', 'a']}, ctx.run({'items': ['B', 'a', 'c']})\n"
            "print('ok')\n",
        ],
        capture_output=True,
        text=True,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert "ok" in smoke.stdout

    # Determinism: re-render must produce identical hashes.
    again = r.render(pattern_plan=PLAN, templates=templates, target_root=str(tmp_path))
    assert again.manifest_sha256 == manifest.manifest_sha256


# --- Java strategy (structure-only) ---------------------------------


def test_java_strategy_templates_render_with_expected_class_names(tmp_path: Path) -> None:
    if not JAVA_TMPL_DIR.exists():
        pytest.skip("Java strategy templates not present")
    r = PatternTemplateRenderer()
    templates = _load_templates(JAVA_TMPL_DIR)
    manifest = r.render(pattern_plan=PLAN, templates=templates, target_root=str(tmp_path))
    java_files = list(tmp_path.glob("*.java"))
    assert java_files, "no .java files rendered"
    combined = "\n".join(f.read_text(encoding="utf-8") for f in java_files)
    # Package and class names must be substituted, not literal placeholders.
    assert "demo.strategy" in combined
    assert "OrderStrategy" in combined
    assert "OrderContext" in combined
    # No Jinja-style placeholders left over
    assert "${" not in combined
    # Manifest hashes match
    for rendered in manifest.files:
        on_disk = (tmp_path / rendered.output_path).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == rendered.sha256


# --- TypeScript strategy (structure-only) ---------------------------


def test_typescript_strategy_templates_render_with_expected_types(tmp_path: Path) -> None:
    if not TS_TMPL_DIR.exists():
        pytest.skip("TypeScript strategy templates not present")
    r = PatternTemplateRenderer()
    templates = _load_templates(TS_TMPL_DIR)
    manifest = r.render(pattern_plan=PLAN, templates=templates, target_root=str(tmp_path))
    ts_files = list(tmp_path.glob("*.ts"))
    assert ts_files, "no .ts files rendered"
    combined = "\n".join(f.read_text(encoding="utf-8") for f in ts_files)
    assert "OrderStrategy" in combined
    assert "OrderContext" in combined
    # No unrendered placeholders
    assert "${" not in combined
    # Vitest test file must import describe/it/expect
    test_combined = "\n".join(
        f.read_text(encoding="utf-8") for f in ts_files if f.name.endswith(".test.ts")
    )
    assert "from 'vitest'" in test_combined
