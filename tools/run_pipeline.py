import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

# Ensure tools directory is importable when run directly
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.process_todos import (  # type: ignore
    ROLE_TO_CONFIG,
    render_prompt,
    load_json_file,
    save_json_file,
    append_history,
    FOLLOW_UP_SUGGESTIONS,
)
from common.http_client import http_post

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
TODO_PATHS = [
    os.path.join(PROJECT_ROOT, "todo.json"),
]
TODO_NEXT_PATH = os.path.join(PROJECT_ROOT, "todo_next.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "tasks_history", "pipeline")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _gather_tasks() -> List[Tuple[str, str]]:
    """Return list of (role_alias, task_text) from all todo files."""
    tasks: List[Tuple[str, str]] = []
    for p in TODO_PATHS:
        data = load_json_file(p) or {}
        for item in data.get("todos", []):
            role = (item.get("role") or "").strip()
            t = (item.get("task") or "").strip()
            if role and t:
                tasks.append((role, t))
    return tasks


def _select_api_endpoint(cfg: Dict[str, Any], agent_models: List[str]) -> Tuple[str, str]:
    """Pick an API endpoint URL and model.
    Prefers endpoints that list one of agent_models; otherwise fall back to the first endpoint's first model.
    Returns (url, model).
    """
    endpoints = cfg.get("api_endpoints", []) or []
    if not endpoints:
        return "", ""
    # try match by agent model
    for ep in endpoints:
        ep_models = ep.get("models", []) or []
        if not agent_models:
            continue
        for m in agent_models:
            if m in ep_models:
                return ep.get("url") or "", m
    # fallback to first endpoint first model
    first = endpoints[0]
    url = first.get("url") or ""
    model = (first.get("models", []) or [""])[0]
    return url, model


def _compose_payload(ep_type: str, model: str, prompt: str) -> Dict[str, Any]:
    # For now only support LM Studio completions-compatible API
    if ep_type.lower() == "lmstudio" or ep_type.lower() == "lm studio":
        return {
            "model": model,
            "prompt": prompt,
            "max_tokens": 512,
            "temperature": 0.2,
            "stream": False,
        }
    # Default generic format
    return {"model": model, "prompt": prompt}


def run_pipeline(dry_run: bool = False, verbose: bool = True) -> None:
    cfg: Dict[str, Any] = load_json_file(CONFIG_PATH) or {}
    prompt_templates: Dict[str, str] = cfg.get("prompt_templates", {})
    agents: Dict[str, Any] = cfg.get("agents", {})
    order: List[str] = cfg.get("pipeline_order", []) or []
    endpoints: List[Dict[str, Any]] = cfg.get("api_endpoints", []) or []

    all_tasks = _gather_tasks()
    if verbose:
        print(f"Collected {len(all_tasks)} tasks from {len(TODO_PATHS)} todo files.")

    # Group tasks by config role key, but keep original role alias with each task
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for role_alias, task in all_tasks:
        normalized = role_alias.strip().lower()
        config_role = ROLE_TO_CONFIG.get(normalized)
        if not config_role:
            continue
        grouped.setdefault(config_role, []).append((normalized, task))

    _ensure_dir(OUTPUT_DIR)

    # Will collect deduplicated follow-up todos for next iteration
    next_todos_set = set()  # Set[Tuple[str, str]] of (role_alias, task)

    for role_key in order:
        agent_cfg = agents.get(role_key, {})
        template = prompt_templates.get(role_key) or prompt_templates.get("default", "{task}")
        agent_models = agent_cfg.get("models", []) or cfg.get("models", []) or []

        if verbose:
            print("\n=== Agent:", role_key)

        tasks = grouped.get(role_key, [])
        if not tasks:
            if verbose:
                print("No tasks for this agent. Skipping.")
            continue

        # Choose endpoint and model once per agent
        url, model = _select_api_endpoint(cfg, agent_models)
        ep_type = ""
        for ep in endpoints:
            if ep.get("url") == url:
                ep_type = ep.get("type", "")
                break
        if verbose:
            print(f"Using endpoint: {url or 'N/A'} | model: {model or 'N/A'}")

        for idx, (role_alias_norm, task_text) in enumerate(tasks, start=1):
            # include role purpose/description in prompt rendering
            role_purpose = (agents.get(role_key, {}) or {}).get("purpose")
            prompt = render_prompt(template, role_key, task_text, role_purpose)
            out: Dict[str, Any] = {
                "timestamp": now_iso(),
                "agent": role_key,
                "role_alias": role_alias_norm,
                "task": task_text,
                "prompt": prompt,
                "endpoint_url": url,
                "model": model,
                "status": "skipped" if dry_run else "pending",
            }

            response_obj: Any = None
            error_msg: str = ""

            if not dry_run and url and model:
                try:
                    payload = _compose_payload(ep_type, model, prompt)
                    response_obj = http_post(url, payload, retries=1, delay=0.25, timeout=30.0)
                    out["status"] = "ok" if response_obj is not None else "error"
                except Exception as e:  # pragma: no cover - network/environment
                    out["status"] = "error"
                    error_msg = str(e)
            elif not url or not model:
                out["status"] = "error" if not dry_run else out["status"]
                error_msg = error_msg or "No API endpoint/model configured"

            if response_obj is not None:
                # Try to keep both raw and text
                out["response"] = response_obj
            if error_msg:
                out["error"] = error_msg

            # Write artifact
            safe_role = role_key.lower().replace("/", "_").replace(" ", "_")
            filename = f"{now_iso()}_{safe_role}_{idx:02d}.json".replace(":", "-")
            with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)

            # Update per-role history with processed task
            try:
                append_history(role_alias_norm, [task_text])
            except Exception:
                # History append should not break pipeline; continue silently
                pass

            # Collect follow-up suggestions for todo_next by config role
            suggestions = FOLLOW_UP_SUGGESTIONS.get(role_key, [])
            for s in suggestions:
                pair = (role_alias_norm, s.strip())
                if pair[1]:
                    next_todos_set.add(pair)

            if verbose:
                print(f"Processed task {idx}/{len(tasks)} | status: {out['status']}")

    # Persist todo_next.json with deduplicated suggestions
    try:
        todos_list = [{"role": role, "task": task} for (role, task) in sorted(next_todos_set)]
        save_json_file(TODO_NEXT_PATH, {"todos": todos_list})
        if verbose:
            print(f"Wrote {len(todos_list)} next todos to: {TODO_NEXT_PATH}")
    except Exception as e:
        if verbose:
            print(f"Failed to write todo_next.json: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run pipeline defined in config.json over todo tasks.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the LM endpoint; just generate prompts and artifacts")
    parser.add_argument("--quiet", action="store_true", help="Less console output")

    args = parser.parse_args()
    run_pipeline(dry_run=args.dry_run, verbose=not args.quiet)
