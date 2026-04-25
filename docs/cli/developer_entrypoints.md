# Developer Entry Points (Compatibility)

Fuer normale Nutzer gilt der Golden Path ueber `ananta ...`.

Die folgenden Modulaufrufe bleiben als **interner/dev fallback** erhalten:

```bash
python -m agent.cli_goals --status
python -m agent.cli_goals --first-run
python -m agent.cli_goals ask "Repository-Status pruefen"
```

## Kompatibilitaetsregel

- Neue Nutzerdokumentation zeigt `ananta ...` als Standard.
- `python -m agent.cli_goals ...` bleibt fuer Dev/Tests und Rueckwaertskompatibilitaet verfuegbar.
