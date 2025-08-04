"""Dashboard utilities with class and interface based design."""

from __future__ import annotations

import json
from typing import Protocol, Callable, Dict, Any
from flask import Request


class ConfigInterface(Protocol):
    """Simple interface for configuration access."""

    def read(self) -> Dict[str, Any]:
        ...

    def write(self, config: Dict[str, Any]) -> None:
        ...


class FileConfig(ConfigInterface):
    """File based implementation of :class:`ConfigInterface`."""

    def __init__(self, reader: Callable[[], Dict[str, Any]], writer: Callable[[Dict[str, Any]], None]):
        self._reader = reader
        self._writer = writer

    def read(self) -> Dict[str, Any]:
        return self._reader()

    def write(self, config: Dict[str, Any]) -> None:
        self._writer(config)


class DashboardManager:
    """Encapsulates dashboard mutation logic."""

    def __init__(self, config: ConfigInterface, default_agent: Dict[str, Any], providers: list[str]):
        self.config = config
        self.default_agent = default_agent
        self.providers = providers

    def handle_post(self, req: Request) -> None:
        cfg = self.config.read()
        self._reorder_pipeline(cfg, req)
        self._manage_tasks(cfg, req)
        self._handle_new_agent(cfg, req)
        self._handle_active_agent(cfg, req)
        self._update_agent_config(cfg, req)
        self._update_endpoints(cfg, req)
        self._update_templates(cfg, req)
        self.config.write(cfg)

    def _reorder_pipeline(self, cfg: Dict[str, Any], req: Request) -> None:
        move_agent = req.form.get("move_agent")
        direction = req.form.get("direction")
        if move_agent and direction in ("up", "down"):
            order = cfg.get("pipeline_order", [])
            if move_agent not in order:
                order.append(move_agent)
            idx = order.index(move_agent)
            if direction == "up" and idx > 0:
                order[idx - 1], order[idx] = order[idx], order[idx - 1]
            elif direction == "down" and idx < len(order) - 1:
                order[idx + 1], order[idx] = order[idx], order[idx + 1]
            cfg["pipeline_order"] = order

    def _manage_tasks(self, cfg: Dict[str, Any], req: Request) -> None:
        task_action = req.form.get("task_action")
        task_idx = req.form.get("task_idx")
        tasks = cfg.setdefault("tasks", [])
        if task_action and task_idx is not None:
            try:
                idx = int(task_idx)
            except ValueError:
                idx = None
            if idx is not None and 0 <= idx < len(tasks):
                if task_action == "move_up" and idx > 0:
                    tasks[idx - 1], tasks[idx] = tasks[idx], tasks[idx - 1]
                elif task_action == "move_down" and idx < len(tasks) - 1:
                    tasks[idx + 1], tasks[idx] = tasks[idx], tasks[idx + 1]
                elif task_action == "start":
                    task = tasks.pop(idx)
                    tasks.insert(0, task)
                elif task_action == "skip":
                    tasks.pop(idx)
        elif req.form.get("add_task"):
            text = req.form.get("task_text", "").strip()
            agent_field = req.form.get("task_agent", "").strip() or None
            if text:
                tasks.append({"task": text, "agent": agent_field})

    def _handle_new_agent(self, cfg: Dict[str, Any], req: Request) -> None:
        new_agent = req.form.get("new_agent", "").strip()
        if new_agent and new_agent not in cfg["agents"]:
            cfg["agents"][new_agent] = self.default_agent.copy()
            cfg.setdefault("pipeline_order", []).append(new_agent)

    def _handle_active_agent(self, cfg: Dict[str, Any], req: Request) -> None:
        set_active = req.form.get("set_active")
        if set_active and set_active in cfg["agents"]:
            cfg["active_agent"] = set_active

    def _update_agent_config(self, cfg: Dict[str, Any], req: Request) -> None:
        agent_name = req.form.get("agent") or cfg.get("active_agent")
        agent_cfg = cfg["agents"].setdefault(agent_name, self.default_agent.copy())
        for key, default in self.default_agent.items():
            if key == "tasks":
                val = req.form.get("tasks")
                if val is not None:
                    agent_cfg["tasks"] = [t.strip() for t in val.splitlines() if t.strip()]
            else:
                val = req.form.get(key)
                if val is None:
                    continue
                if isinstance(default, bool):
                    agent_cfg[key] = (val or "").lower() == "true"
                elif isinstance(default, int):
                    try:
                        agent_cfg[key] = int(val)
                    except Exception:
                        pass
                elif isinstance(default, str):
                    agent_cfg[key] = val

    def _update_endpoints(self, cfg: Dict[str, Any], req: Request) -> None:
        if req.form.get("api_endpoints_form"):
            endpoints = []
            for i, ep in enumerate(cfg.get("api_endpoints", [])):
                if req.form.get(f"endpoint_delete_{i}"):
                    continue
                typ = req.form.get(f"endpoint_type_{i}") or ep.get("type")
                url = req.form.get(f"endpoint_url_{i}") or ep.get("url")
                if typ and url:
                    endpoints.append({"type": typ, "url": url})
            new_type = req.form.get("new_endpoint_type")
            new_url = req.form.get("new_endpoint_url")
            if req.form.get("add_endpoint") and new_url:
                endpoints.append({"type": new_type or self.providers[0], "url": new_url})
            cfg["api_endpoints"] = endpoints

    def _update_templates(self, cfg: Dict[str, Any], req: Request) -> None:
        templates_field = req.form.get("prompt_templates")
        if templates_field is not None:
            try:
                cfg["prompt_templates"] = json.loads(templates_field)
            except Exception:
                pass
