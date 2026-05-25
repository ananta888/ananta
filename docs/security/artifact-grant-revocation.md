# Artifact Grant Revocation und Expiry

## Grundregeln

- Jeder Grant kann ein `expires_at` haben.
- Revocation setzt den Grantstatus sofort auf `revoked`.
- Expiry und Revocation stoppen **neue** Zugriffsausgaben.

## Wirkung von Expiry

- Nach Ablauf: keine neuen Download-Tickets, keine neuen CEK-Unwraps, keine neuen Context-Releases.
- Bereits ausgestellte, noch gueltige Kurzzeit-Tickets muessen serverseitig erneut gegen Grantstatus geprueft werden.

## Wirkung von Revocation

- Neue Schluessel-/Download-Ausgaben werden sofort geblockt.
- Neue Worker-/LLM-Kontextfreigaben werden sofort geblockt.
- Abgeleitete Delegationen koennen entlang `parent_grant_id` ebenfalls gesperrt werden.

## Grenzen der Rueckholung

- Bereits lokal entschluesselte Kopien sind nicht vollstaendig rueckholbar.
- Diese Grenze muss explizit dokumentiert und im UI sichtbar kommuniziert werden.

## Audit-Anforderungen

- Audit muss zeigen:
  - wer den Grant erteilt hat,
  - wer Zugriff vor Widerruf genutzt hat,
  - wann Revocation wirksam wurde,
  - welche Folgeanfragen danach abgelehnt wurden.
