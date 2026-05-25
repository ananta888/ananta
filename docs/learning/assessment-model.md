# Assessment Model

## Assessment-Typen

- Quiz (Wissensabfrage)
- Policy-Entscheidungen (allow/deny mit Begruendung)
- Artefakt-Review (Qualitaet + Sicherheitsgrenzen)
- Praktische Sandbox-Uebung

## Deterministische Checks

- Erwartete Ergebnisse sind explizit hinterlegt.
- Sicherheitssensitive Freigaben duerfen nicht nur von LLM-Bewertung abhaengen.
- LLM-Feedback ist optionaler Tutor, nicht Autorisierungsquelle.

## Fehlversuche

- `max_attempts` pro Assessment definierbar.
- Nach Ueberschreitung: `review_required` statt stiller Eskalation.
- Retry kann mit Mentor-Review gekoppelt werden.

## Output

Jeder Lauf liefert:

- Ergebnis (`passed|failed|review_required`)
- Check-Protokoll
- erkannte Sicherheitsverletzungen
- empfohlene naechste sichere Uebung
