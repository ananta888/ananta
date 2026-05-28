from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MermaidRenderResult:
    success: bool
    image_data: bytes | None
    image_format: str
    fallback_text: str
    reason: str
    duration_ms: float


def _check_mmdc() -> str | None:
    return shutil.which("mmdc")


def _check_playwright() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


class MermaidCliBackend:
    name = "mermaid_cli"

    def available(self) -> tuple[bool, str]:
        path = _check_mmdc()
        return (True, "") if path else (False, "mmdc not found in PATH")

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
                success=False,
                image_data=None,
                image_format="",
                fallback_text=source,
                reason="mmdc not found in PATH",
                duration_ms=0.0,
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            in_file = Path(tmpdir) / "diagram.mmd"
            out_file = Path(tmpdir) / "diagram.svg"
            in_file.write_text(source, encoding="utf-8")
            try:
                proc = subprocess.run(
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
                        duration_ms=round(elapsed_ms, 2),
                    )
                reason = proc.stderr.decode(errors="replace").strip() or "mmdc exited non-zero"
                return MermaidRenderResult(
                    success=False,
                    image_data=None,
                    image_format="",
                    fallback_text=source,
                    reason=reason,
                    duration_ms=round(elapsed_ms, 2),
                )
            except subprocess.TimeoutExpired:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return MermaidRenderResult(
                    success=False,
                    image_data=None,
                    image_format="",
                    fallback_text=source,
                    reason=f"timeout after {timeout_seconds}s",
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
                    duration_ms=round(elapsed_ms, 2),
                )


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
            if result.success or name == "fallback_codeblock":
                return result
        return MermaidRenderResult(
            success=False,
            image_data=None,
            image_format="",
            fallback_text=source,
            reason="no renderer available",
            duration_ms=0.0,
        )
