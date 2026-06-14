#!/usr/bin/env python3
"""
Generates docs/ananta-ui-guide.md from the codebase.

Sources (all deterministic, no LLM required):
  - frontend-angular/src/app/models/route-metadata.ts  → nav structure
  - data-waypoint attributes in Angular .ts/.html files → UI element registry
  - control-center.routes.ts                           → CC sub-navigation
  - agent/routes/*.py                                  → Flask API endpoints
  - client_surfaces/operator_tui/chat_state.py         → DEFAULT_SESSIONS

Optional: pass --llm to have the Hub enrich descriptions (falls back to raw data).

Usage:
    python scripts/generate_ui_guide.py [--llm] [--out docs/ananta-ui-guide.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DEFAULT = REPO_ROOT / "docs" / "ananta-ui-guide.md"

# ── helpers ──────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ── 1. Nav structure from route-metadata.ts ──────────────────────────────────

def extract_nav_items() -> list[dict]:
    src = _read(REPO_ROOT / "frontend-angular/src/app/models/route-metadata.ts")
    # Match lines like:  chats: { label: 'AI Chats', area: 'Operate', navGroup: 'Arbeiten', ...
    pattern = re.compile(
        r"'?([\w-]+)'?\s*:\s*\{[^}]*label:\s*['\"]([^'\"]+)['\"][^}]*navGroup:\s*['\"]([^'\"]+)['\"][^}]*\}",
        re.DOTALL,
    )
    items = []
    for m in pattern.finditer(src):
        path, label, group = m.group(1), m.group(2), m.group(3)
        expert = "expertOnly: true" in m.group(0)
        admin = "adminOnly: true" in m.group(0)
        items.append({"path": path, "label": label, "group": group, "expert": expert, "admin": admin})
    return items


# ── 2. Control-Center sub-routes ─────────────────────────────────────────────

def extract_cc_routes() -> list[dict]:
    src = _read(REPO_ROOT / "frontend-angular/src/app/features/control-center/control-center.routes.ts")
    pattern = re.compile(r"\{[^}]*path:\s*['\"]([^'\"]+)['\"][^}]*component:\s*(\w+)[^}]*\}", re.DOTALL)
    results = []
    for m in pattern.finditer(src):
        path, comp = m.group(1), m.group(2)
        if path and path not in ("", "**"):
            results.append({"path": path, "component": comp})
    return results


# ── 3. data-waypoint registry ─────────────────────────────────────────────────

def extract_waypoints() -> list[dict]:
    waypoints: list[dict] = []
    pattern = re.compile(r'data-waypoint=["\']([^"\']+)["\'](?:[^>]*)>([^<]{0,60})', re.DOTALL)
    attr_pattern = re.compile(r'\[attr\.data-waypoint\]=["\']\s*[\'"]([^"\']+)["\']', re.DOTALL)

    for f in sorted((REPO_ROOT / "frontend-angular/src").rglob("*.ts")):
        text = _read(f)
        for m in pattern.finditer(text):
            wp = m.group(1).strip()
            label = re.sub(r"\s+", " ", m.group(2)).strip()
            if wp:
                waypoints.append({"waypoint": wp, "label": label or wp, "file": f.name})
        # dynamic waypoints like [attr.data-waypoint]="'nav.' + item.path"
        for m in attr_pattern.finditer(text):
            wp = m.group(1).strip()
            if wp and not any(w["waypoint"] == wp for w in waypoints):
                waypoints.append({"waypoint": wp, "label": "(dynamisch)", "file": f.name})

    for f in sorted((REPO_ROOT / "frontend-angular/src").rglob("*.html")):
        text = _read(f)
        for m in pattern.finditer(text):
            wp = m.group(1).strip()
            label = re.sub(r"\s+", " ", m.group(2)).strip()
            if wp and not any(w["waypoint"] == wp for w in waypoints):
                waypoints.append({"waypoint": wp, "label": label or wp, "file": f.name})

    # deduplicate by waypoint name (keep first)
    seen: set[str] = set()
    unique = []
    for w in waypoints:
        if w["waypoint"] not in seen:
            seen.add(w["waypoint"])
            unique.append(w)
    return unique


# ── 4. Flask API routes ───────────────────────────────────────────────────────

_SKIP_ROUTE_FILES = {"_auth_test_routes.py", "_auth_mfa_routes.py", "blender_client_surface.py"}
_INCLUDE_PREFIXES = ("/api/", "/chat", "/snake", "/sessions", "/goals", "/workers",
                     "/policies", "/codecompass", "/model", "/routing", "/health", "/config",
                     "/teams/", "/templates", "/blueprints", "/snakes")


def extract_api_routes() -> list[dict]:
    routes: list[dict] = []
    route_pat = re.compile(r'@\w+\.route\(["\']([^"\']+)["\'](?:.*?methods=\[([^\]]+)\])?', re.DOTALL)
    bp_pat = re.compile(r"(\w+)_bp\s*=\s*Blueprint\(['\"]([^'\"]+)['\"].*?url_prefix=['\"]([^'\"]+)['\"]", re.DOTALL)

    for f in sorted((REPO_ROOT / "agent/routes").glob("*.py")):
        if f.name in _SKIP_ROUTE_FILES or f.name.startswith("_"):
            continue
        text = _read(f)
        # find blueprint prefix
        prefix = ""
        bm = bp_pat.search(text)
        if bm:
            prefix = bm.group(3).rstrip("/")

        for m in route_pat.finditer(text):
            path = prefix + m.group(1)
            methods_raw = m.group(2) or "GET"
            methods = re.findall(r"['\"]([A-Z]+)['\"]", methods_raw)
            if not methods:
                methods = ["GET"]
            if any(path.startswith(p) for p in _INCLUDE_PREFIXES):
                routes.append({"path": path, "methods": methods, "file": f.name})

    # deduplicate
    seen: set[tuple] = set()
    unique = []
    for r in routes:
        key = (r["path"], tuple(r["methods"]))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return sorted(unique, key=lambda r: r["path"])


# ── 5. Feature documentation index (docs/*.md) ────────────────────────────────

# Docs files whose first heading describes a concrete user-facing feature.
# Grouped loosely by topic prefix.
_FEATURE_DOC_TOPICS: dict[str, list[str]] = {
    "Blueprint": ["blueprint", "standard-blueprints"],
    "Vorlagen / Templates": ["template-authoring", "template-role-overlay", "template-variable"],
    "Worker": ["worker-contract", "worker-directory", "worker-extension", "worker-routing"],
    "CodeCompass": ["codecompass", "codecompass-architecture", "codecompass-domain"],
    "Instruction Layers": ["instruction-layer"],
    "Pair Dev / Sharing": ["operator-tui-shared-sessions"],
    "Auto-Planner": ["auto-planner"],
    "API": ["hub-api", "api-goal", "api-auth"],
    "Policies": ["context_access_policy", "planning-agent-governance"],
    "LLM / Routing": ["llm-routing", "llm-provider-config", "llm-observability", "ollama-model-routing"],
}


def extract_feature_docs() -> dict[str, list[dict]]:
    """Return {topic: [{filename, title, excerpt}]} for known feature docs."""
    docs_dir = REPO_ROOT / "docs"
    result: dict[str, list[dict]] = {}
    for topic, prefixes in _FEATURE_DOC_TOPICS.items():
        entries = []
        for f in sorted(docs_dir.glob("*.md")):
            if any(f.stem.startswith(p) or f.stem == p for p in prefixes):
                text = _read(f)
                # first H1 or H2
                title_m = re.search(r"^#{1,2}\s+(.+)$", text, re.MULTILINE)
                title = title_m.group(1).strip() if title_m else f.stem
                # first non-heading non-empty line as excerpt
                lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
                excerpt = lines[0].strip()[:120] if lines else ""
                entries.append({"file": f.name, "title": title, "excerpt": excerpt})
        if entries:
            result[topic] = entries
    return result


# ── 6. DEFAULT_SESSIONS from chat_state.py ────────────────────────────────────

def extract_sessions() -> list[dict]:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from client_surfaces.operator_tui.chat_state import DEFAULT_SESSIONS
        return [
            {
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "icon": s.get("icon", "💬"),
                "group": s.get("group", ""),
                "system_prompt_excerpt": (s.get("system_prompt") or "")[:120].replace("\n", " "),
            }
            for s in DEFAULT_SESSIONS
        ]
    except Exception as e:
        return [{"id": "?", "name": f"Fehler beim Laden: {e}", "icon": "", "group": "", "system_prompt_excerpt": ""}]


# ── 6. Current live config (user.json) ────────────────────────────────────────

def extract_live_config() -> dict:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        s = get_manager().load()
        sessions = s.get("chat_sessions") or []
        backend = s.get("chat_backend", "?")
        model = s.get("chat_backend_model", "?")
        active_sid = s.get("chat_active_session_id", "")
        routing = s.get("model_routing_rules") or s.get("routing_rules") or []
        return {
            "backend": backend,
            "model": model,
            "active_session": active_sid,
            "session_count": len(sessions),
            "routing_rules": routing,
        }
    except Exception:
        return {}


# ── Markdown renderer ─────────────────────────────────────────────────────────

def render(
    nav_items: list[dict],
    cc_routes: list[dict],
    waypoints: list[dict],
    api_routes: list[dict],
    sessions: list[dict],
    feature_docs: dict[str, list[dict]] | None = None,
    live_cfg: dict | None = None,
) -> str:
    lines: list[str] = [
        "# Ananta UI & Konfigurations-Guide",
        "",
        "> Automatisch generiert aus dem Ananta-Quellcode.",
        "> Zeigt Navigation, UI-Elemente, Chat-Sessions und API-Endpoints.",
        "",
    ]

    # ── Navigation ──
    lines += ["## Hauptnavigation", ""]
    groups: dict[str, list[dict]] = {}
    for item in nav_items:
        groups.setdefault(item["group"], []).append(item)

    for group, items in sorted(groups.items()):
        lines.append(f"### {group}")
        for item in sorted(items, key=lambda x: x["label"]):
            flags = []
            if item.get("expert"):
                flags.append("Experte")
            if item.get("admin"):
                flags.append("Admin")
            flag_str = f" _{', '.join(flags)}_" if flags else ""
            lines.append(f"- **{item['label']}** → `/{item['path']}`{flag_str}")
        lines.append("")

    # ── Control Center ──
    if cc_routes:
        lines += ["## Control Center (Unterseiten)", ""]
        lines.append("Erreichbar über `/control-center` → linkes Menü:")
        for r in cc_routes:
            lines.append(f"- `{r['path']}` — {r['component'].replace('ControlCenter', '').replace('Component', '')}")
        lines.append("")

    # ── Waypoints ──
    lines += ["## UI-Elemente (Waypoints)", ""]
    lines.append("Diese Bezeichner identifizieren konkrete Schaltflächen/Bereiche in der Oberfläche:")
    lines.append("")

    wp_groups: dict[str, list[dict]] = {}
    for w in waypoints:
        prefix = w["waypoint"].split(".")[0]
        wp_groups.setdefault(prefix, []).append(w)

    for prefix, wps in sorted(wp_groups.items()):
        lines.append(f"**{prefix}.**")
        for w in sorted(wps, key=lambda x: x["waypoint"]):
            label = w["label"].strip().strip("{}").strip()
            lines.append(f"  - `{w['waypoint']}` — {label}")
        lines.append("")

    # ── Chat Sessions ──
    lines += ["## Chat-Sessions (Typen)", ""]
    sess_groups: dict[str, list[dict]] = {}
    for s in sessions:
        sess_groups.setdefault(s.get("group") or "Allgemein", []).append(s)

    for grp, sess in sorted(sess_groups.items()):
        lines.append(f"### {grp}")
        for s in sess:
            lines.append(f"- {s['icon']} **{s['name']}** (`{s['id']}`): {s['system_prompt_excerpt']}…")
        lines.append("")

    # ── Feature Documentation Index ──
    if feature_docs:
        lines += ["## Feature-Dokumentation (docs/)", ""]
        lines.append("Folgende Dokumentationsdateien beschreiben die wichtigsten Features:")
        lines.append("")
        for topic, entries in sorted(feature_docs.items()):
            lines.append(f"### {topic}")
            for e in entries:
                lines.append(f"- **{e['title']}** (`{e['file']}`): {e['excerpt']}")
            lines.append("")

    # ── Live Config ──
    if live_cfg:
        lines += ["## Aktuelle Konfiguration (live)", ""]
        lines.append(f"- Aktives Backend: `{live_cfg.get('backend', '?')}`")
        lines.append(f"- Modell: `{live_cfg.get('model', '?')}`")
        lines.append(f"- Aktive Session: `{live_cfg.get('active_session') or '(keine)'}`")
        lines.append(f"- Chat-Sessions gesamt: {live_cfg.get('session_count', 0)}")
        if live_cfg.get("routing_rules"):
            lines.append(f"- Routing-Regeln: {len(live_cfg['routing_rules'])} konfiguriert")
        lines.append("")

    # ── API Endpoints ──
    if api_routes:
        lines += ["## Hub-API Endpoints (Auswahl)", ""]
        file_groups: dict[str, list[dict]] = {}
        for r in api_routes:
            file_groups.setdefault(r["file"], []).append(r)

        for fname, routes in sorted(file_groups.items()):
            lines.append(f"**{fname}**")
            for r in routes:
                methods = ", ".join(r["methods"])
                lines.append(f"  - `{methods} {r['path']}`")
            lines.append("")

    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────

def generate(out_path: Path = OUTPUT_DEFAULT, include_live: bool = False) -> Path:
    """Generate the UI guide markdown.

    Args:
        out_path: where to write the file.
        include_live: if True, append a live-config snapshot (stale after generation).
                      Leave False when the caller will inject live data separately.
    """
    nav_items = extract_nav_items()
    cc_routes = extract_cc_routes()
    waypoints = extract_waypoints()
    api_routes = extract_api_routes()
    sessions = extract_sessions()
    feature_docs = extract_feature_docs()
    live_cfg = extract_live_config() if include_live else None

    md = render(nav_items, cc_routes, waypoints, api_routes, sessions, feature_docs, live_cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Ananta UI guide from codebase")
    parser.add_argument("--out", default=str(OUTPUT_DEFAULT), help="Output path")
    parser.add_argument("--llm", action="store_true", help="Enrich with LLM (not yet implemented)")
    args = parser.parse_args()

    out = generate(Path(args.out))
    print(f"Generiert: {out} ({out.stat().st_size} Bytes)")
