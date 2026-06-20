from __future__ import annotations

import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time

from agent.config import settings
from agent.research_backend import is_research_backend, run_research_backend_command
from agent.common.sgpt_helpers import (
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_ollama_openai_base_url,
    _normalize_openai_base_url,
    _resolve_profile_api_key,
)
from agent.common.sgpt_backend_semaphore import (
    _BACKEND_SEMAPHORES,
    _SemaphoreTicket,
    _acquire_backend_permit,
    _get_backend_semaphore,
)
from agent.cli_backends.routing import (
    SUPPORTED_CLI_BACKENDS,
    CLI_BACKEND_INSTALL_HINTS,
    CLI_BACKEND_VERIFY_COMMANDS,
    CLI_BACKEND_CAPABILITIES,
    _BACKEND_RUNTIME,
    _choose_candidates,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
    get_research_backend_preflight,
    normalize_backend_flags,
)
from agent.common.sgpt_opencode import (
    resolve_codex_runtime_config,
    resolve_opencode_runtime_config,
    run_aider_command,
    run_codex_command,
    run_mistral_code_command,
    run_opencode_command,
)
from agent.common.sgpt_architecture_scan import (
    _MAX_LINE_WINDOW,
    _bounded_worker_int,
    _build_iteration_prompt,
    _format_block_header,
    _is_architecture_full_scan_context,
    _load_source_file_batches,
    _read_research_context,
    _resolve_repo_root,
    _run_architecture_full_scan,
)
from agent.llm_integration_ollama import probe_ollama_runtime, resolve_ollama_model
from agent.llm_integration_lmstudio import probe_lmstudio_runtime

log = logging.getLogger(__name__)


def run_sgpt_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    model: str | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """
    Führt einen SGPT-Befehl zentral aus, inkl. korrekter Environment-Injektion.
    Gibt (returncode, stdout, stderr) zurück.
    """
    options = options or []
    if "--no-interaction" not in options:
        options.append("--no-interaction")

    agent_cfg = _get_agent_config()
    selected_model = (
        str(
            model
            or agent_cfg.get("sgpt_default_model")
            or agent_cfg.get("default_model")
            or agent_cfg.get("model")
            or settings.sgpt_default_model
            or ""
        ).strip()
        or None
    )
    args = (["--model", selected_model] if selected_model else []) + options + [prompt]

    with _acquire_backend_permit("sgpt", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'sgpt' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()

        runtime_provider = _get_runtime_default_provider()
        provider_urls = _get_runtime_provider_urls()

        base_url = None
        if runtime_provider == "ollama":
            base_url = _normalize_ollama_openai_base_url(provider_urls.get("ollama") or settings.ollama_url)
        elif runtime_provider == "lmstudio":
            base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
        elif runtime_provider == "openai":
            base_url = _normalize_openai_base_url(provider_urls.get("openai") or settings.openai_url)

        if base_url:
            env["OPENAI_API_BASE"] = base_url
            env["OPENAI_BASE_URL"] = base_url
        else:
            env.pop("OPENAI_API_BASE", None)
            env.pop("OPENAI_BASE_URL", None)

        if not env.get("OPENAI_API_KEY"):
            configured_api_key = (
                _resolve_profile_api_key(str(agent_cfg.get("openai_api_key_profile") or "").strip())
                or str(settings.openai_api_key or "").strip()
                or None
            )
            if configured_api_key:
                env["OPENAI_API_KEY"] = configured_api_key
            elif runtime_provider in {"lmstudio", "ollama"} or _is_probably_local_base_url(base_url):
                env["OPENAI_API_KEY"] = "sk-no-key-needed"

        try:
            log.info(f"Zentraler SGPT-Aufruf: {args}")
            cwd = workdir if (workdir and pathlib.Path(workdir).is_dir()) else None
            result = subprocess.run(  # noqa: S603 - args are constructed in-process; no shell=True
                [sys.executable, "-m", "sgpt"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                cwd=cwd,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            log.error("SGPT Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            log.exception(f"SGPT Fehler: {e}")
            return -1, "", str(e)


def _run_ananta_worker_iterative(
    prompt: str,
    workdir: str | None,
    *,
    options: list,
    timeout: int,
    model: str | None,
    files_per_batch: int = 3,
    per_file_chars: int = 4_000,
    max_iterations: int = 8,
) -> tuple[int, str, str]:
    """Iterative execution loop for ananta-worker."""
    # AWWPI-013/014: workspace mutation loop takes precedence when enabled and
    # the resolved mutation mode is not read_only. Falls back to the batch loop
    # on any setup error so existing behavior never breaks.
    try:
        from agent.common.sgpt_workspace_mutation import (
            get_workspace_mutation_config,
            run_ananta_worker_workspace_mutation,
        )

        mutation_cfg = get_workspace_mutation_config(workdir)
        if mutation_cfg.get("enabled") and workdir and str(mutation_cfg.get("resolved_mode") or "read_only") != "read_only":
            return run_ananta_worker_workspace_mutation(
                prompt,
                workdir,
                options=options,
                timeout=timeout,
                model=model,
                config=mutation_cfg,
            )
    except Exception:
        log.warning("workspace mutation loop unavailable, falling back", exc_info=True)

    # AWTCL-010/011: hub-controlled tool loop behind a feature flag; the
    # context batch loop below stays the default and the fallback.
    try:
        from agent.cli_backends.tool_loop import get_tool_loop_config, run_ananta_worker_tool_loop

        tool_loop_cfg = get_tool_loop_config()
        if tool_loop_cfg.get("enabled"):
            return run_ananta_worker_tool_loop(
                prompt,
                workdir,
                options=options,
                timeout=timeout,
                model=model,
                config=tool_loop_cfg,
            )
    except Exception:
        log.warning("tool loop unavailable, falling back to batch loop", exc_info=True)

    files_per_batch = _bounded_worker_int("ananta_worker_context_files_per_batch", files_per_batch, 1, 20)
    per_file_chars = _bounded_worker_int("ananta_worker_context_per_file_chars", per_file_chars, 500, 40_000)
    max_iterations = _bounded_worker_int("ananta_worker_context_max_iterations", max_iterations, 1, 32)
    context_lines = _bounded_worker_int("ananta_worker_context_line_window", 5, 0, _MAX_LINE_WINDOW)
    max_snippet_chars = _bounded_worker_int("ananta_worker_context_max_snippet_chars", 8_000, 200, 40_000)
    research_context = _read_research_context(workdir)
    if workdir and _is_architecture_full_scan_context(research_context):
        return _run_architecture_full_scan(
            prompt,
            workdir,
            options=options,
            timeout=timeout,
            model=model,
            research_context=research_context,
        )

    batches = _load_source_file_batches(
        workdir,
        files_per_batch=files_per_batch,
        per_file_chars=per_file_chars,
        max_files=max_iterations * files_per_batch,
        context_lines=context_lines,
        max_snippet_chars=max_snippet_chars,
    )

    if not batches:
        return run_sgpt_command(prompt=prompt, options=options, timeout=timeout, model=model, workdir=workdir)

    if len(batches) == 1:
        batch = batches[0]
        file_blocks = "\n\n".join(
            f"{_format_block_header(b)}\n```{b.get('lang', 'text')}\n{b.get('content', '')}\n```"
            for b in batch
        )
        enriched = f"{prompt.rstrip()}\n\n---\n\n{file_blocks}"
        return run_sgpt_command(prompt=enriched, options=options, timeout=timeout, model=model, workdir=workdir)

    capped = batches[:max_iterations]
    total = len(capped)
    progress_path = (pathlib.Path(workdir) / "rag_helper" / "progress.md") if workdir else None
    progress_parts: list[str] = []
    last_rc, last_out, last_err = 0, "", ""

    for step, batch in enumerate(capped, start=1):
        iter_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            batch=batch,
            progress_so_far="\n\n---\n\n".join(progress_parts),
            step=step,
            total_steps=total,
            is_synthesis=False,
        )
        rc, out, err = run_sgpt_command(
            prompt=iter_prompt, options=options, timeout=timeout, model=model, workdir=workdir
        )
        last_rc, last_err = rc, err
        if out:
            last_out = out
            source_labels = []
            for b in batch:
                sk = b.get("source_kind") or "file_excerpt"
                rp = b.get("rel_path") or ""
                s, e = b.get("start_line"), b.get("end_line")
                if s is not None and e is not None:
                    source_labels.append(f"{rp}:{s}-{e} [{sk}]")
                else:
                    source_labels.append(f"{rp} [{sk}]")
            step_header = f"## Schritt {step} — {', '.join(source_labels)}"
            progress_parts.append(f"{step_header}\n\n{out.strip()}")
            if progress_path:
                try:
                    progress_path.parent.mkdir(parents=True, exist_ok=True)
                    progress_path.write_text(
                        "\n\n---\n\n".join(progress_parts), encoding="utf-8"
                    )
                except OSError:
                    pass
        if rc != 0 and not out:
            log.warning("ananta-worker iteration %s/%s failed (rc=%s), stopping early", step, total, rc)
            break

    if progress_parts:
        synthesis_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            batch=[],
            progress_so_far="\n\n---\n\n".join(progress_parts),
            step=total + 1,
            total_steps=total + 1,
            is_synthesis=True,
        )
        rc, out, err = run_sgpt_command(
            prompt=synthesis_prompt, options=options, timeout=timeout, model=model, workdir=workdir
        )
        if out:
            last_rc, last_out, last_err = rc, out, err
            if progress_path:
                try:
                    final_text = (
                        "\n\n---\n\n".join(progress_parts)
                        + f"\n\n---\n\n## Finales Ergebnis\n\n{out.strip()}"
                    )
                    progress_path.write_text(final_text, encoding="utf-8")
                except OSError:
                    pass

    return last_rc, last_out, last_err


def run_llm_cli_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    backend: str = "ananta-worker",
    model: str | None = None,
    temperature: float | None = None,
    routing_policy: dict | None = None,
    research_context: dict | None = None,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str, str]:
    """
    Führt den konfigurierten CLI-Backend-Aufruf aus.
    Rückgabe: (returncode, stdout, stderr, backend_used)
    """
    requested = (backend or "ananta-worker").strip().lower()
    candidates = _choose_candidates(requested=requested, prompt=prompt, routing_policy=routing_policy)

    def _normalize_opencode_model_identifier(value: str | None) -> str | None:
        normalized = str(value or "").strip() or None
        if not normalized or "/" in normalized:
            return normalized
        provider = str(getattr(settings, "default_provider", "") or _get_runtime_default_provider()).strip().lower()
        if provider in {"openai", "anthropic", "gemini", "groq", "openrouter", "bedrock", "azure", "vertexai", "copilot"}:
            return f"{provider}/{normalized}"
        return normalized

    last_error = ""
    now = time.time()
    for name in candidates:
        started = time.time()
        if name == "sgpt":
            rc, out, err = run_sgpt_command(prompt=prompt, options=options or [], timeout=timeout, model=model, workdir=workdir)
        elif name == "ananta-worker":
            rc, out, err = _run_ananta_worker_iterative(
                prompt=prompt,
                workdir=workdir,
                options=options or [],
                timeout=timeout,
                model=model,
            )
        elif name == "codex":
            rc, out, err = run_codex_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "opencode":
            rc, out, err = run_opencode_command(
                prompt=prompt,
                model=_normalize_opencode_model_identifier(model),
                timeout=timeout,
                session=session,
                workdir=workdir,
            )
        elif name == "aider":
            rc, out, err = run_aider_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "mistral_code":
            rc, out, err = run_mistral_code_command(prompt=prompt, model=model, timeout=timeout)
        elif is_research_backend(name):
            rc, out, err = run_research_backend_command(
                prompt=prompt,
                model=model,
                temperature=temperature,
                timeout=timeout,
                provider=name,
                research_context=research_context,
            )
        else:
            continue

        rt = _BACKEND_RUNTIME.setdefault(name, {})
        rt["last_rc"] = rc
        rt["last_latency_ms"] = int((time.time() - started) * 1000)
        if rc == 0 or out:
            rt["last_success_at"] = now
            rt["consecutive_failures"] = 0
            rt["cooldown_until"] = 0.0
            rt["total_success"] = int(rt.get("total_success", 0)) + 1
            rt["last_error"] = ""
            return rc, out, err, name
        rt["last_failure_at"] = now
        rt["consecutive_failures"] = int(rt.get("consecutive_failures", 0)) + 1
        rt["total_failures"] = int(rt.get("total_failures", 0)) + 1
        rt["last_error"] = err or f"{name} failed with exit code {rc}"
        cooldown = min(120, 10 * (2 ** max(0, rt["consecutive_failures"] - 1)))
        rt["cooldown_until"] = time.time() + cooldown
        last_error = err or f"{name} failed with exit code {rc}"

    return -1, "", last_error or "No CLI backend succeeded", candidates[-1] if candidates else requested
