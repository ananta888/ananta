"""TUI renderer for VectorEncoding and TransformerFeatureProvider status (TQ-020).

Renders the ``vector_encoding`` section of the CodeCompass diagnostics as a
compact, colourised multi-line view.  The renderer is a pure function of the
diagnostics dict — it never calls the hub or any external service.

Usage::

    diag = orchestrator.last_diagnostic()
    view = render_vector_encoding_status(diag.get("vector_encoding"), colour=True)
    print(view.text)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"
_MAGENTA = "\x1b[35m"
_BLUE = "\x1b[34m"


@dataclass(frozen=True)
class VectorEncodingStatusView:
    text: str
    summary_line: str
    mode: str
    experimental: bool
    enabled: bool


def render_vector_encoding_status(
    encoding_diag: dict[str, Any] | None,
    *,
    colour: bool = False,
    transformer_feature_mode: str | None = None,
) -> VectorEncodingStatusView:
    """Render a compact TUI view of VectorEncoding + TransformerFeatureProvider status.

    Args:
        encoding_diag: the ``vector_encoding`` sub-dict from ``last_diagnostic()``.
        colour: whether to apply ANSI colour codes.
        transformer_feature_mode: value of CODECOMPASS_TRANSFORMER_FEATURE_MODE.
    """
    diag = dict(encoding_diag or {})
    mode = str(diag.get("mode") or "off")
    enabled = bool(diag.get("enabled", False))
    experimental = bool(diag.get("experimental", False))
    profile_hash = str(diag.get("profile_hash") or "")[:12]
    fallback = str(diag.get("fallback_policy") or "fallback_float32")
    ratio = diag.get("compression_ratio")
    max_err = diag.get("max_abs_error")
    tf_mode = str(transformer_feature_mode or diag.get("transformer_feature_mode") or "disabled")

    def c(code: str, text: str) -> str:
        if not colour:
            return text
        return f"{code}{text}{_RESET}"

    def fmt_mode(m: str) -> str:
        if m in {"off", "float32"}:
            return c(_DIM, m)
        if m == "float16":
            return c(_BLUE, m)
        if m == "int8":
            return c(_GREEN, m)
        if m == "symmetric4bit":
            return c(_YELLOW, m)
        if "turboquant" in m:
            return c(_MAGENTA, m)
        return m

    lines: list[str] = []

    header = c(_BOLD, "VectorEncoding")
    mode_str = fmt_mode(mode)
    status_str = c(_GREEN, "active") if enabled else c(_DIM, "disabled")
    lines.append(f"  {header}  mode={mode_str}  ({status_str})")

    if enabled:
        ratio_str = f"{ratio:.2f}×" if ratio is not None else "—"
        err_str = f"{max_err:.5f}" if max_err is not None else "—"
        lines.append(f"    ratio={ratio_str}  max_err={err_str}  hash={profile_hash}")
        lines.append(f"    fallback_policy={fallback}")
        if experimental:
            lines.append(c(_YELLOW, "    ⚠  experimental mode — do not use in production without benchmark gate"))
    else:
        lines.append(c(_DIM, "    (quantization off — vectors stored as float32)"))

    tf_header = c(_BOLD, "TransformerFeatureProvider")
    if tf_mode == "disabled":
        tf_str = c(_DIM, "disabled")
    elif tf_mode == "observe_only":
        tf_str = c(_CYAN, "observe_only")
    elif tf_mode == "context_first":
        tf_str = c(_GREEN, "context_first")
    else:
        tf_str = c(_YELLOW, tf_mode)
    lines.append(f"  {tf_header}  mode={tf_str}")

    text = "\n".join(lines)

    ratio_part = f"[{ratio:.1f}×]" if (ratio and enabled) else ""
    err_part = f"err={max_err:.3f}" if (max_err is not None and enabled) else ""
    summary = f"VectorEncoding: {mode} {ratio_part} {err_part}  TransformerFeature: {tf_mode}".strip()

    return VectorEncodingStatusView(
        text=text,
        summary_line=summary,
        mode=mode,
        experimental=experimental,
        enabled=enabled,
    )
