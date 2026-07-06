"""Classroom transcript assistant (track classroom-transcript-codecompass-assistant).

Pipeline: Event (Webhook/MCP/Batch) -> Gateway (TriggerEngine-Dedup,
PII-Redaction) -> Context-Hints -> Frageerkennung -> Modul/Task-Resolver
-> Antwort-Komposition -> optional n8n-Selektor + Verifier ->
TeacherActionCard. Read-only: Vorschlag und Export, keine Einspielung
in Schueler-n8n (siehe CTA-009).
"""
