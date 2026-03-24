# Goal-first Overview

Kurz: Das System unterstützt jetzt ein Goal-zentriertes Bedienkonzept. Nutzer geben ein Ziel an; das System erzeugt einen Plan, zerlegt ihn in Tasks, delegiert an Worker, verifiziert Ergebnisse und liefert Artefakte mit Prüfnachweisen.

Migration: Bestehende Task-APIs bleiben erhalten. Die Goal-Option ist additive und per Feature-Flag konfigurierbar (config.json -> feature_flags.goal_workflow_enabled). Operators können das Verhalten stufenweise aktivieren.

Beispiele und einfache cURL-Aufrufe folgen in der Operator-Dokumentation.