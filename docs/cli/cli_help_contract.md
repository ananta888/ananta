# Ananta CLI Help-Vertrag

Jede Command-Gruppe und jedes Leaf-Command muss diesen Vertrag erfüllen.
Ein Command gilt **nicht als stabil**, bevor sein Help-Output getestet ist.

## Pflichtregeln

1. **Jedes Top-Level-Command** unterstützt `--help` und liefert Exit Code 0.
2. **Jede Command-Gruppe** unterstützt `--help` und listet alle direkten Subcommands auf.
3. **Jedes Leaf-Command** unterstützt `--help` und dokumentiert Purpose, Options, Defaults, Examples und Exit Codes.
4. **Help darf keine Runtime-Abhängigkeiten haben:**
   - `--help` muss ohne `config.json` funktionieren.
   - `--help` muss ohne laufenden Hub funktionieren.
   - `--help` muss ohne laufenden Worker funktionieren.
   - `--help` muss ohne Docker funktionieren.
   - `--help` muss ohne Netzwerkzugang funktionieren.
   - `--help` muss in einem leeren temporären Verzeichnis funktionieren.
5. **Kein `--help` darf Fachlogik ausführen** (keine DB-Schreibzugriffe, keine Netzwerkaufrufe, keine Prozesse starten).

## Pflichtinhalt je Command-Ebene

### Command-Gruppe (`ananta <domain> --help`)

```
Usage: ananta <domain> [options] <action> ...

<Beschreibung des fachlichen Zwecks — 1-3 Sätze>

Actions:
  <action1>   <Kurzbeschreibung>
  <action2>   <Kurzbeschreibung>
  ...

Options:
  -h, --help  Show this help.

Examples:
  ananta <domain> <action> [...]
```

### Leaf-Command (`ananta <domain> <action> --help`)

```
Usage: ananta <domain> <action> [options]

Purpose: <Was macht dieser Befehl — ein Satz>

Options:
  --<option>  <Beschreibung>  (default: <Wert>)
  ...
  -h, --help  Show this help.

Exit codes:
  0   success
  1   runtime failure
  2   invalid arguments
  3   validation failed
  4   external dependency unavailable
  5   security/policy violation

Examples:
  ananta <domain> <action> --<option> <value>
```

## Exit Code Vertrag

| Code | Bedeutung | Wann verwenden |
|------|-----------|----------------|
| 0 | Success | Normale Ausführung, `--help` |
| 1 | Runtime failure | Unerwarteter Fehler, API-Fehler |
| 2 | Invalid arguments / CLI usage error | Falsche/fehlende Argumente |
| 3 | Validation failed | Schema/Strukturfehler (config, plan) |
| 4 | External dependency unavailable | Hub nicht erreichbar, Skript fehlt |
| 5 | Security or policy violation | Policy-Block, Sicherheitsregel |
| 10 | Internal bug | Unerwarteter Fehler in der CLI selbst |

## Mutating vs. Read-only

- **Mutierende Commands** sind in der Help-Ausgabe mit `[MUTATING]` markiert.
- `--help` auf einem mutierenden Command darf **keine Mutation** auslösen.
- Mutierende Commands sollen `--dry-run` unterstützen, wenn sinnvoll.
- Mutierende Commands zeigen eine klare Bestätigung nach Ausführung.

## Secret-Maskierung

- API Keys, Tokens, Authorization Header dürfen **nicht im Klartext** ausgegeben werden.
- `--verbose` darf Secrets nicht unmaskiert ausgeben.
- `--json` darf Secrets nicht unmaskiert ausgeben.
- Typische Secret-Felder: `access_token`, `password`, `api_key`, `Authorization`.

## Automatische Tests

Alle registrierten Command-Pfade werden in `tests/cli/test_help_contract.py` geprüft:

- Jede Domain-Gruppe: `dispatch(["--help"])` → Exit 0, nicht-leere Ausgabe, enthält Subcommands.
- Jedes Leaf-Command: `dispatch([subcmd, "--help"])` → Exit 0, nicht-leere Ausgabe.
- `ananta --help` über main() → Exit 0, erwähnt alle Domain-Gruppen.
- Netzwerk-Blockierungstest: `--help` darf keine Sockets öffnen.

Ein neues Command **schlägt diese Tests fehl**, bis es korrekt implementiert ist.
Das ist der eingebaute Qualitätsgate-Mechanismus.

## Namenskonventionen für Options

| Standard | Beispiel |
|----------|---------|
| `--json` | Alle Commands mit strukturierter Ausgabe |
| `--verbose` / `-v` | Commands mit Diagnoseausgabe |
| `--quiet` / `-q` | Commands in CI/Skript-Kontext |
| `--dry-run` | Mutierende Commands |
| `--base-url` | Commands die den Hub ansprechen |
| `--user` / `--password` | Commands mit Auth |
| `--goal-id` | Commands die eine Goal-ID benötigen |
| `--task-id` | Commands die eine Task-ID benötigen |

## Interaktive ID-Auflösung

Commands, die eine Goal- oder Task-ID benötigen, unterstützen:

1. **Kein Argument**: Zeigt interaktiven Arrow-Key-Picker über die letzten N Einträge.
2. **UUID-Präfix** (z.B. `4c77`): Filtert und wählt automatisch, wenn eindeutig;
   zeigt Picker bei mehreren Treffern.
3. **Vollständige UUID**: Direktes Lookup, kein Picker.

Implementiert in `agent/cli/utils/interactive.py`.
