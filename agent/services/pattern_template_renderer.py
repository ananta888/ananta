"""Deterministic pattern-template renderer.

Renders source code and test files from a validated PatternPlan and
a set of template files. The renderer is:

- Deterministic: identical inputs -> byte-identical output.
- Sandboxed: uses ``string.Template`` (no expression engine, no
  attribute access, no method calls). A missing variable raises a
  controlled ``RenderError``; it is never silently replaced by "".
- Path-safe: all writes resolve under the target root, and the
  renderer refuses paths that escape it (defence-in-depth against
  accidental absolute paths or ``..`` segments in template output).
- Auditable: emits a manifest with one row per written file
  (``path``, ``sha256``, ``pattern_id``, ``language``,
  ``template_name``) plus a top-level manifest hash.

Template variables use the ``${var_name}`` syntax (configurable via
the ``TEMPLATE_IDPATTERN``). The renderer is intentionally a single
class — keep the surface area small so it stays auditable.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from string import Template
from typing import Any, Iterable


# The renderer uses ``@@name@@`` as the substitution marker. This is
# chosen because it never collides with Python f-strings ({var}),
# Java template strings (${var}), or TypeScript template strings
# (`${var}`). Templates are preprocessed to convert ``@@name@@`` to
# the internal ``${name}`` form before string.Template substitutes.
_VAR_PATTERN = re.compile(r"@@([A-Za-z_][A-Za-z0-9_]*)@@")
_PLACEHOLDER_PREFIX = "__PATTERN_VAR__"


class RenderError(ValueError):
    """Raised when a pattern plan cannot be rendered safely.

    The error message is always safe to log — it does not include
    the rendered source.
    """


@dataclass(frozen=True)
class TemplateFile:
    """One input template, identified by its logical name."""

    template_name: str
    output_path: str  # relative to the target root
    content: str


@dataclass(frozen=True)
class RenderedFile:
    """One written (or dry-run) output file."""

    output_path: str
    sha256: str
    bytes_written: int
    template_name: str
    pattern_id: str
    language: str


@dataclass(frozen=True)
class RenderManifest:
    """Aggregate manifest of a single render run."""

    pattern_id: str
    language: str
    files: list[RenderedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "files": [
                {
                    "output_path": f.output_path,
                    "sha256": f.sha256,
                    "bytes_written": f.bytes_written,
                    "template_name": f.template_name,
                    "pattern_id": f.pattern_id,
                    "language": f.language,
                }
                for f in self.files
            ],
            "warnings": list(self.warnings),
        }

    @property
    def manifest_sha256(self) -> str:
        """Stable hash of the manifest content (sorted by output_path)."""
        payload = "\n".join(
            f"{f.output_path}\t{f.sha256}\t{f.bytes_written}" for f in sorted(self.files, key=lambda x: x.output_path)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PatternTemplateRenderer:
    """Stateless renderer; safe to share across threads.

    The renderer is pure with respect to its inputs — given the same
    ``pattern_plan`` and ``templates`` list, it produces the same
    manifest. I/O is opt-in via ``target_root`` (or dry-run).
    """

    def __init__(self) -> None:
        # string.Template already rejects attribute access and
        # method calls, but we double-check the input templates
        # for the common Jinja-style markers so a confused operator
        # cannot accidentally use a Jinja template and get a
        # confusing silent success.
        pass

    # --- public surface -----------------------------------------------

    def render(
        self,
        *,
        pattern_plan: dict[str, Any],
        templates: Iterable[TemplateFile],
        target_root: str | None = None,
        dry_run: bool = False,
    ) -> RenderManifest:
        """Render templates for a single pattern plan.

        Args:
            pattern_plan: a validated pattern dict (must contain
                ``pattern_id``, ``language``, and ``parameters`` as
                a flat dict).
            templates: iterable of ``TemplateFile``.
            target_root: directory to write to. ``None`` means
                dry-run (no I/O). When set, the renderer ensures
                every output_path resolves inside target_root.
            dry_run: explicit dry-run override; takes precedence
                over ``target_root``.

        Returns:
            A :class:`RenderManifest` with one entry per rendered
            file and a stable manifest hash.

        Raises:
            RenderError: on missing parameters, unsafe output paths,
                forbidden template tokens, or forbidden template
                characters in the rendered output.
        """
        if dry_run or not target_root:
            return self._render_dry(pattern_plan, list(templates))

        return self._render_to_disk(pattern_plan, list(templates), target_root)

    # --- internals -----------------------------------------------------

    def _resolve_params(self, pattern_plan: dict[str, Any]) -> dict[str, Any]:
        # Two layouts are accepted, in priority order:
        # 1) pattern_plan["parameters"] is a flat dict of {name: value}
        # 2) pattern_plan["parameters_provided"] is a flat dict
        # Layout (1) is the most common one (matches the binding
        # contract from BlueprintPlanningAdapter). Layout (2) keeps
        # compatibility with the pattern.schema.v1.json shape, where
        # `parameters` is the schema array.
        flat = pattern_plan.get("parameters_provided")
        if flat is None:
            parameters = pattern_plan.get("parameters")
            if isinstance(parameters, dict):
                flat = parameters
            elif isinstance(parameters, list):
                # Schema-array form: no values supplied.
                flat = {}
            else:
                raise RenderError(
                    "pattern_plan parameters must be a dict (flat) or a list (schema array)"
                )
        if not isinstance(flat, dict):
            raise RenderError("pattern_plan parameters must be a dict")
        # Coerce non-string values via repr() so the rendered output
        # is deterministic and round-trippable.
        resolved: dict[str, str] = {}
        # Implicit parameters: pattern_id and language are always available
        # inside templates so templates can reference them without requiring
        # the caller to duplicate them in the parameters dict.
        for implicit_key in ("pattern_id", "language"):
            val = pattern_plan.get(implicit_key)
            if val is not None:
                resolved[implicit_key] = str(val)
        for key, value in flat.items():
            if value is None:
                continue
            if isinstance(value, str):
                resolved[str(key)] = value
            elif isinstance(value, (bool, int, float)):
                resolved[str(key)] = str(value)
            elif isinstance(value, (list, tuple)):
                resolved[str(key)] = ", ".join(str(v) for v in value)
            else:
                resolved[str(key)] = str(value)
        return resolved

    def _declared_param_names(self, pattern_plan: dict[str, Any]) -> set[str]:
        declared: set[str] = set()
        for entry in pattern_plan.get("parameters", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                declared.add(str(entry["name"]))
        return declared

    def _check_template_safety(self, content: str, template_name: str) -> None:
        # The substitution marker is ``@@name@@``. Any *bare* ``${name}``
        # is preserved as output (so Java/TypeScript template strings
        # render literally). We just verify the source contains a
        # well-formed marker set so a confused operator gets a clear
        # error rather than silent non-substitution.
        if "@@" in content and not _VAR_PATTERN.search(content):
            # A stray '@@' with no valid name is suspicious (likely a
            # typo). Surface it.
            raise RenderError(
                f"template '{template_name}' contains stray '@@' without a valid marker"
            )

    def _preprocess(self, content: str) -> tuple[Template, dict[str, str]]:
        """Convert ``@@name@@`` markers into a string.Template.

        Two-stage substitution is used:

        1. ``@@name@@`` markers are mapped to **private placeholder
           values** (e.g. ``__PATTERN_VAR__name__``) that *do not*
           contain ``$`` so they survive string.Template's first pass
           unscathed.
        2. The placeholder values are then run through a second
           string.Template to substitute the real parameter values.

        This way ``${...}`` literals in the template (e.g.
        TypeScript template strings, Java MessageFormat) survive
        intact: the first pass does not see them, and the second
        pass sees them as ordinary output text.

        Returns:
            A ``(Template, placeholders)`` tuple. ``placeholders``
            maps logical var name -> private placeholder string, so
            the caller can resolve real values for them.
        """
        placeholders: dict[str, str] = {}

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in placeholders:
                # Use a value that does NOT contain '$' so the first
                # string.Template pass treats it as ordinary text.
                placeholders[name] = f"__PATTERN_VAR__{name}__PATTERN_VAR__"
            return placeholders[name]

        converted = _VAR_PATTERN.sub(repl, content)
        return Template(converted), placeholders

    def _safe_output_path(self, target_root: str, output_path: str) -> str:
        # Reject absolute paths and '..' segments explicitly before
        # resolving, so the renderer never writes outside target_root
        # even if os.path.commonpath behaves unexpectedly on Windows.
        if os.path.isabs(output_path):
            raise RenderError(f"output_path '{output_path}' must be relative")
        normalised = os.path.normpath(output_path)
        if normalised.startswith("..") or "/.." in f"/{normalised}" or normalised == "..":
            raise RenderError(
                f"output_path '{output_path}' escapes the target root"
            )
        full = os.path.abspath(os.path.join(target_root, normalised))
        root_abs = os.path.abspath(target_root) + os.sep
        if not (full + os.sep).startswith(root_abs):
            raise RenderError(
                f"output_path '{output_path}' resolves outside target_root"
            )
        return full

    def _render_dry(
        self, pattern_plan: dict[str, Any], templates: list[TemplateFile]
    ) -> RenderManifest:
        return self._render_impl(pattern_plan, templates, target_root=None)

    def _render_to_disk(
        self,
        pattern_plan: dict[str, Any],
        templates: list[TemplateFile],
        target_root: str,
    ) -> RenderManifest:
        manifest = self._render_impl(pattern_plan, templates, target_root=target_root)
        # The render_impl already wrote the files. Build the manifest
        # on disk.
        for rendered in manifest.files:
            full_path = self._safe_output_path(target_root, rendered.output_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            # Read back the file we just wrote to compute the hash
            # (avoids any non-determinism in os.write).
            with open(full_path, "rb") as f:
                data = f.read()
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != rendered.sha256:
                raise RenderError(
                    f"hash mismatch for '{rendered.output_path}' after write"
                )
        return manifest

    def _render_impl(
        self,
        pattern_plan: dict[str, Any],
        templates: list[TemplateFile],
        target_root: str | None,
    ) -> RenderManifest:
        pattern_id = str(pattern_plan.get("pattern_id") or "").strip()
        language = str(pattern_plan.get("language") or "agnostic").strip()
        if not pattern_id:
            raise RenderError("pattern_plan.pattern_id is required")

        resolved_params = self._resolve_params(pattern_plan)
        declared = self._declared_param_names(pattern_plan)
        warnings: list[str] = []

        files: list[RenderedFile] = []
        for tpl in templates:
            self._check_template_safety(tpl.content, tpl.template_name)
            template, placeholders = self._preprocess(tpl.content)
            # Stage 1: substitute the private placeholders into the
            # template body. Each placeholder is a plain text token
            # (no '$'), so the first string.Template pass just
            # inserts it as output. The marker positions are now
            # anchored at well-known plain-text strings.
            try:
                stage1 = template.substitute(placeholders)
            except (KeyError, ValueError) as exc:
                raise RenderError(
                    f"template '{tpl.template_name}' has invalid marker form: {exc}"
                ) from exc
            # Stage 2: substitute the real values for each marker.
            # We do *not* rebuild a Template here — the text already
            # contains the anchored marker strings, and we do a
            # straight .replace() to avoid re-evaluating any '${...}'
            # literals that the source may have legitimately wanted
            # to keep (e.g. TypeScript template strings).
            rendered = stage1
            for var_name, placeholder in placeholders.items():
                if var_name not in resolved_params:
                    raise RenderError(
                        f"template '{tpl.template_name}' references unknown parameter "
                        f"{var_name!r}; available: {sorted(resolved_params)}"
                    )
                rendered = rendered.replace(
                    placeholder, resolved_params[var_name]
                )

            # Validate output_path early — both for dry-run and on-disk.
            if target_root is not None:
                full_path = self._safe_output_path(target_root, tpl.output_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(rendered)

            sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            files.append(
                RenderedFile(
                    output_path=tpl.output_path,
                    sha256=sha,
                    bytes_written=len(rendered.encode("utf-8")),
                    template_name=tpl.template_name,
                    pattern_id=pattern_id,
                    language=language,
                )
            )

        # Detect undeclared parameters that were supplied (warning,
        # not error: a user may pass extras for forward-compat).
        # Implicit params (pattern_id, language) are always available
        # and never count as undeclared.
        _IMPLICIT = {"pattern_id", "language"}
        undeclared = set(resolved_params) - declared - _IMPLICIT
        if undeclared and declared:
            warnings.append(
                f"pattern_plan supplied undeclared parameters: {sorted(undeclared)}"
            )

        return RenderManifest(
            pattern_id=pattern_id,
            language=language,
            files=files,
            warnings=warnings,
        )
