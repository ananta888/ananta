from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from client_runtime import process


@dataclass(frozen=True)
class MermaidRenderResult:
    success: bool
    image_data: bytes | None
    image_format: str
    fallback_text: str
    reason: str
    duration_ms: float
    reason_code: str = ""
    backend_used: str = ""


def _check_mmdc() -> str | None:
    return shutil.which("mmdc")


def _check_playwright() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


_KNOWN_DIAGRAM_TYPES = frozenset({
    "flowchart", "graph", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "journey", "gantt", "pie", "requirementdiagram",
    "gitgraph", "mindmap", "timeline", "quadrantchart", "xychart-beta",
    "sankey-beta", "block-beta", "architecture-beta",
})


def _has_diagram_type(source: str) -> bool:
    """Return True if the first non-empty line declares a known diagram type."""
    for line in source.splitlines():
        stripped = line.strip().lower()
        if not stripped or stripped.startswith("%%"):
            continue
        first_word = stripped.split()[0]
        return first_word in _KNOWN_DIAGRAM_TYPES
    return False


def _auto_fix_source(source: str) -> str | None:
    """Try to auto-fix common Mermaid issues. Returns fixed source or None."""
    import re

    fixed = source

    # Fix "flowchart direction=TB" or "flowchart dir=LR" → "flowchart TB"
    fixed = re.sub(
        r'^(flowchart|graph)\s+(?:direction=|dir=)(\w+).*$',
        lambda m: f"{m.group(1)} {m.group(2).upper()}",
        fixed, flags=re.MULTILINE | re.IGNORECASE,
    )
    # Fix "flowchart direction LR" → "flowchart LR"
    fixed = re.sub(r'^(flowchart|graph)\s+direction\s+(\w+)', r'\1 \2', fixed, flags=re.MULTILINE | re.IGNORECASE)
    # Remove unsupported attributes like nameLabel="..." from first line
    fixed = re.sub(r'^(flowchart|graph\s+\w+).*?(nameLabel|title|config)\s*=\s*"[^"]*".*$',
                   r'\1', fixed, flags=re.MULTILINE | re.IGNORECASE)
    # Fix invalid arrow syntax "-- Yes::>" → "-- Yes -->"
    fixed = re.sub(r'--\s*(\w[^:>]*?)::>', r'-- \1 -->', fixed)
    # Fix " : wenn yes" style labels (invalid) → strip them
    fixed = re.sub(r'\s+:\s+\w.*$', '', fixed, flags=re.MULTILINE)
    # Strip lines that are clearly non-Mermaid (HTML comments, XML tags)
    lines_out = [l for l in fixed.splitlines() if not re.match(r'^\s*(<[^>]+>|<!--)', l)]
    fixed = "\n".join(lines_out)

    # If still no diagram type, prepend flowchart TD
    if not _has_diagram_type(fixed):
        fixed = "flowchart TD\n" + fixed

    return fixed if fixed != source else None


def _compact_error(reason: str, max_len: int = 80) -> str:
    """Extract the first meaningful line of an mmdc error, truncated."""
    for line in reason.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("at ") and "node_modules" not in stripped:
            return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    return reason[:max_len]


class MermaidCliBackend:
    name = "mermaid_cli"

    def available(self) -> tuple[bool, str]:
        path = _check_mmdc()
        return (True, "") if path else (False, "mmdc not found in PATH")

    def _run_mmdc(
        self,
        source: str,
        mmdc: str,
        *,
        timeout_seconds: float,
        width: int,
        height: int,
    ) -> MermaidRenderResult:
        """Run mmdc for a single source string. Returns result (success or failure)."""
        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmpdir:
            in_file = Path(tmpdir) / "diagram.mmd"
            out_file = Path(tmpdir) / "diagram.svg"
            in_file.write_text(source, encoding="utf-8")
            try:
                proc = process.run(
                    [mmdc, "-i", str(in_file), "-o", str(out_file), "-w", str(width), "-H", str(height)],
                    capture_output=True,
                    timeout=timeout_seconds,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                if proc.returncode == 0 and out_file.exists():
                    return MermaidRenderResult(
                        success=True,
                        image_data=out_file.read_bytes(),
                        image_format="svg",
                        fallback_text="",
                        reason="",
                        reason_code="OK",
                        backend_used=self.name,
                        duration_ms=round(elapsed_ms, 2),
                    )
                raw_err = proc.stderr.decode(errors="replace").strip() or "mmdc exited non-zero"
                return MermaidRenderResult(
                    success=False, image_data=None, image_format="",
                    fallback_text=source,
                    reason=_compact_error(raw_err),
                    reason_code="MMDC_RENDER_FAILED",
                    backend_used=self.name,
                    duration_ms=round(elapsed_ms, 2),
                )
            except process.TimeoutExpired:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return MermaidRenderResult(
                    success=False, image_data=None, image_format="",
                    fallback_text=source,
                    reason=f"timeout nach {timeout_seconds:.0f}s",
                    reason_code="MMDC_TIMEOUT",
                    backend_used=self.name,
                    duration_ms=round(elapsed_ms, 2),
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return MermaidRenderResult(
                    success=False, image_data=None, image_format="",
                    fallback_text=source,
                    reason=_compact_error(str(exc)),
                    reason_code="MMDC_EXEC_ERROR",
                    backend_used=self.name,
                    duration_ms=round(elapsed_ms, 2),
                )

    def render(
        self,
        source: str,
        *,
        timeout_seconds: float = 15.0,
        width: int = 1280,
        height: int = 720,
    ) -> MermaidRenderResult:
        start = time.perf_counter()
        mmdc = _check_mmdc()
        if not mmdc:
            return MermaidRenderResult(
                success=False, image_data=None, image_format="",
                fallback_text=source, reason="mmdc nicht im PATH", reason_code="MMD_NOT_FOUND", backend_used=self.name, duration_ms=0.0,
            )
        # First attempt with original source
        result = self._run_mmdc(source, mmdc, timeout_seconds=timeout_seconds, width=width, height=height)
        if result.success:
            return result

        # Auto-fix: try common corrections (missing diagram type, wrong syntax)
        fixed = _auto_fix_source(source)
        if fixed and fixed != source:
            fixed_result = self._run_mmdc(fixed, mmdc, timeout_seconds=timeout_seconds, width=width, height=height)
            if fixed_result.success:
                return fixed_result

        return result


class PlaywrightBackend:
    name = "playwright"

    def available(self) -> tuple[bool, str]:
        return (True, "") if _check_playwright() else (False, "playwright package not installed")

    def render(
        self,
        source: str,
        *,
        timeout_seconds: float = 15.0,
        width: int = 1280,
        height: int = 720,
    ) -> MermaidRenderResult:
        start = time.perf_counter()
        if not _check_playwright():
            return MermaidRenderResult(
                success=False,
                image_data=None,
                image_format="",
                fallback_text=source,
                reason="playwright package not installed",
                reason_code="PLAYWRIGHT_MISSING",
                backend_used=self.name,
                duration_ms=0.0,
            )
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]

            mermaid_js = _locate_mermaid_js()
            if mermaid_js is None:
                return MermaidRenderResult(
                    success=False,
                    image_data=None,
                    image_format="",
                    fallback_text=source,
                    reason="mermaid.min.js not found; install via 'npm install mermaid' in project root",
                    reason_code="MERMAID_JS_MISSING",
                    backend_used=self.name,
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                )
            html = _build_mermaid_html(source, mermaid_js, width, height)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": width, "height": height})
                page.set_content(html, wait_until="networkidle")
                data = page.screenshot(type="png")
                browser.close()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return MermaidRenderResult(
                success=True,
                image_data=data,
                image_format="png",
                fallback_text="",
                reason="",
                reason_code="OK",
                backend_used=self.name,
                duration_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return MermaidRenderResult(
                success=False,
                image_data=None,
                image_format="",
                fallback_text=source,
                reason=str(exc),
                reason_code="PLAYWRIGHT_RENDER_FAILED",
                backend_used=self.name,
                duration_ms=round(elapsed_ms, 2),
            )


def _locate_mermaid_js() -> Path | None:
    candidates = [
        Path("node_modules/mermaid/dist/mermaid.min.js"),
        Path("node_modules/.bin/../mermaid/dist/mermaid.min.js"),
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return None


def _build_mermaid_html(source: str, mermaid_js: Path, width: int, height: int) -> str:
    js_content = mermaid_js.read_text(encoding="utf-8")
    safe_source = source.replace("</", "<\\/")
    return (
        "<!DOCTYPE html><html><head>"
        f"<style>body{{margin:0;padding:0;background:#fff;}}"
        f".mermaid{{width:{width}px;height:{height}px;}}</style>"
        f"<script>{js_content}</script>"
        "</head><body>"
        f'<div class="mermaid">{safe_source}</div>'
        "<script>mermaid.initialize({startOnLoad:true,securityLevel:'sandbox'});</script>"
        "</body></html>"
    )


class FallbackCodeblockBackend:
    name = "fallback_codeblock"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def render(
        self,
        source: str,
        *,
        timeout_seconds: float = 15.0,
        width: int = 1280,
        height: int = 720,
    ) -> MermaidRenderResult:
        return MermaidRenderResult(
            success=False,
            image_data=None,
            image_format="",
            fallback_text=source,
            reason="Mermaid image renderer unavailable",
            reason_code="FALLBACK_CODEBLOCK",
            backend_used=self.name,
            duration_ms=0.0,
        )


_BACKEND_CLASSES: dict[str, type] = {
    "mermaid_cli": MermaidCliBackend,
    "playwright": PlaywrightBackend,
    "fallback_codeblock": FallbackCodeblockBackend,
}


@dataclass
class MermaidRenderer:
    renderer_order: tuple[str, ...] = ("mermaid_cli", "playwright", "fallback_codeblock")
    timeout_seconds: float = 15.0
    max_width: int = 1280
    max_height: int = 720

    def capability_status(self) -> dict[str, tuple[bool, str]]:
        result: dict[str, tuple[bool, str]] = {}
        for name in self.renderer_order:
            cls = _BACKEND_CLASSES.get(name)
            if cls is None:
                result[name] = (False, f"unknown backend {name!r}")
            else:
                result[name] = cls().available()
        return result

    def render(self, source: str) -> MermaidRenderResult:
        # Preserve the first real-backend failure reason for better diagnostics
        real_backend_failure: str = ""
        for name in self.renderer_order:
            cls = _BACKEND_CLASSES.get(name)
            if cls is None:
                continue
            backend = cls()
            available, _ = backend.available()
            if not available and name != "fallback_codeblock":
                continue
            result = backend.render(
                source,
                timeout_seconds=self.timeout_seconds,
                width=self.max_width,
                height=self.max_height,
            )
            if result.success:
                return result
            if name != "fallback_codeblock" and result.reason and not real_backend_failure:
                real_backend_failure = f"{name}: {result.reason}"
            if name == "fallback_codeblock":
                # If a real backend tried and failed, surface that reason instead of the sentinel
                if real_backend_failure:
                    return MermaidRenderResult(
                        success=False,
                        image_data=None,
                        image_format="",
                        fallback_text=source,
                        reason=real_backend_failure,
                        reason_code="BACKEND_UNAVAILABLE",
                        backend_used=name,
                        duration_ms=result.duration_ms,
                    )
                return result
        return MermaidRenderResult(
            success=False,
            image_data=None,
            image_format="",
            fallback_text=source,
            reason=real_backend_failure or "no renderer available",
            reason_code="NO_RENDERER_AVAILABLE",
            backend_used="",
            duration_ms=0.0,
        )
