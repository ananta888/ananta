# BE-MIG-774: Migration docs & contract tests for legacy task clients

Zweck
-----
Beschreibe, wie ältere Task-getriebene Clients die neuen Goal/Plan/Task-Artefakte nutzen können und wie der Betrieb schrittweise migriert werden kann.

Inhalt (erste Version)
----------------------
- Übersicht: Warum die Migration notwendig ist
- Kompatibilitätsregeln: Felder, die erhalten bleiben, Felder die erweitert werden
- Beispiel-Mappings: Alte Task-Request -> Neue Goal-Workflow
- Contract-Test-Vorlagen: HTTP-Requests und erwartete Antworten (Status-Codes, Schema)
- Feature-Flags: Welche Flags operatorisch zu setzen sind (z.B. goal_workflow_enabled)

Contract-Test Idee
------------------
- Ein einfacher pytest-basierter Contract: Sende alten Task-Post-Request; erwarte, dass API entweder funktioniert (mapping) oder eine deutliche Fehlermeldung liefert mit Anleitung.
- Tests in `tests/test_migration_contracts.py` (Skeleton, optional Skip)

Nächste Schritte
----------------
- Dokumentation committen
- Test-Templates erstellen (optional als Skip, um Test-Suite nicht zu zerschlagen)
