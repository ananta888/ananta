"""Deterministic rule engine for VisualGuide — returns canned tips before hitting the LLM."""

from __future__ import annotations

ROUTE_TIPS: dict[str, str] = {
    "/chats": "Hier kannst du Chat-Sessions auswählen oder neu erstellen.",
    "/teams": "Teams und Blueprints verwalten — Blueprints definieren die Arbeitsweise.",
    "/dashboard": "Dein Überblick: aktive Ziele, Aufgaben und Fortschritt.",
    "/workspace": "Hier arbeitest du direkt mit Zielen und Aufgaben.",
    "/board": "Kanban-Board: alle Tasks auf einen Blick.",
    "/artifacts": "Generierte Ergebnisse und Artefakte.",
    "/control-center": "Control Center: Workers, Sessions und Policy-Entscheidungen.",
}

WAYPOINT_TIPS: dict[str, str] = {
    "chat.new-session": "Neue Chat-Session erstellen.",
    "assistant.tab-ai-snake": "Wechselt zur AI-Snake-Ansicht.",
    "assistant.tab-settings": "Öffnet die Einstellungen der AI-Snake.",
    "snake.tab-explain": "Bereich auswählen — Snake erklärt die Elemente.",
    "nav./dashboard": "Zum Dashboard navigieren.",
}


class RuleEngine:
    """Fast O(1) lookup for deterministic guide tips."""

    def lookup_route(self, route: str) -> str | None:
        """Return a tip for the given route, or None if not found."""
        return ROUTE_TIPS.get(str(route or "").strip()) or None

    def lookup_waypoint(self, waypoint: str) -> str | None:
        """Return a tip for the given waypoint key, or None if not found."""
        return WAYPOINT_TIPS.get(str(waypoint or "").strip()) or None

    def lookup_region_step(self, bubble_label: str) -> str | None:
        """Return an explanation when the bubble_label is a known waypoint, else None."""
        label = str(bubble_label or "").strip()
        # Check exact waypoint match first
        tip = WAYPOINT_TIPS.get(label)
        if tip:
            return tip
        # Substring match: label contains a waypoint key
        for key, val in WAYPOINT_TIPS.items():
            if key in label:
                return val
        return None
