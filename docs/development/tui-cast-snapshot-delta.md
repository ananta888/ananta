# TUI Cast: Snapshot & Delta Pipeline

## Übersicht

Snapshots und Deltas ermöglichen es dem Background Heuristic Lab, den TUI-Screen
zu analysieren ohne den 16ms/tick Fast Path zu blockieren.

## Lokale Erzeugung

### CellGrid aus rendered lines

```python
from client_surfaces.operator_tui.snapshot import CellGrid

lines = renderer.render_operator_shell(state, width=120, height=32).splitlines()
grid = CellGrid.from_rendered_lines(lines)
print(grid.screen_hash, grid.width, grid.height)
```

### Delta zwischen zwei Snapshots

```python
from client_surfaces.operator_tui.snapshot_delta import DeltaEncoder

encoder = DeltaEncoder()
delta = encoder.encode(grid_prev, grid_curr)
print(f"Geänderte Zellen: {delta.changed_cell_count}")
print(f"Geänderte Zeilen: {delta.changed_lines}")

# Delta anwenden
reconstructed = encoder.apply(grid_prev, delta)
assert reconstructed.screen_hash == grid_curr.screen_hash
```

### ANSI-Replay aus PTY-Stream

```python
from client_surfaces.operator_tui.ansi_replay import AnsiReplayState

state = AnsiReplayState(width=120, height=32)
# PTY-Bytes sequentiell anwenden:
for chunk in pty_chunks:
    state.apply_chunk(chunk)
grid = state.to_cell_grid()
```

## Env-Variablen

| Variable | Beschreibung | Default |
|----------|--------------|---------|
| `ANANTA_TUI_RECORD_SNAPSHOTS` | Aktiviert Snapshot-Aufzeichnung | `0` |
| `ANANTA_TUI_SNAPSHOT_DIR` | Ausgabeverzeichnis für Snapshots | `/tmp/ananta_snapshots` |
| `ANANTA_E2E_LIVE_LMSTUDIO` | Aktiviert Live-LMStudio-Tests | nicht gesetzt |

## Demo-Cast vs. PTY-E2E-Cast

### Demo-Cast (für Unit-Tests)

- Basiert auf `list[str]`-Outputs des Renderers
- Kein echtes PTY, kein Prozess-Spawn
- Deterministisch und schnell
- Geeignet für: `tests/operator_tui/`, `tests/e2e/test_tui_snake_snapshot_delta_cast.py`

```python
from client_surfaces.operator_tui.snapshot import CellGrid
lines = ["Hello World", "Second Line"]
grid = CellGrid.from_rendered_lines(lines)
```

### PTY-E2E-Cast (für Integration-Tests)

- Schreibt in echtes Pseudo-Terminal via `pexpect` / `ptyprocess`
- Liest rohe Byte-Streams zurück
- `AnsiReplayState` konvertiert PTY-Stream → CellGrid
- Geeignet für: Timing-Tests, Terminal-Emulations-Tests

```python
import pexpect
child = pexpect.spawn("python -m client_surfaces.operator_tui.main")
chunk = child.read_nonblocking(4096, timeout=0.1).decode("utf-8", errors="replace")
state = AnsiReplayState(width=120, height=32)
state.apply_chunk(chunk)
grid = state.to_cell_grid()
```

## Privacy-Hinweis

**Snapshots können TUI-Text enthalten**, einschließlich:
- Goal-Titel und Task-Namen
- Artifact-Referenzen
- Chat-Nachrichten (wenn Chat-Panel sichtbar)

Snapshots sollten **nicht** in Produktions-Logs gespeichert werden.
Im Test-Modus: nur in temporäre Verzeichnisse schreiben.

## Troubleshooting

### ANSI-Replay produziert falsche Zeichen

**Problem:** `AnsiReplayState` zeigt falsche Zeichen nach SGR-Sequenzen.

**Lösung:** Stelle sicher, dass `apply_chunk()` vollständige Sequenzen erhält.
Partielle ANSI-Sequenzen über Chunk-Grenzen hinweg werden nicht unterstützt.

```python
# Falsch: Chunk endet mitten in ANSI-Sequenz
state.apply_chunk("\x1b[38;2;")
state.apply_chunk("255;0;0m")

# Richtig: Vollständige Sequenz in einem Chunk
state.apply_chunk("\x1b[38;2;255;0;0mHello\x1b[0m")
```

### Delta ergibt falschen Rekonstruierten Screen

**Problem:** `encoder.apply(base, delta).screen_hash != current.screen_hash`

**Mögliche Ursachen:**
1. `base` und `current` haben unterschiedliche Dimensionen (width/height)
2. Cells wurden außerhalb der Grid-Grenzen geändert

**Diagnose:**
```python
delta = encoder.encode(base, current)
print(f"Changed cells: {delta.changed_cell_count}")
print(f"Changed lines: {delta.changed_lines}")
reconstructed = encoder.apply(base, delta)
# Vergleiche Zeilen manuell:
for y in delta.changed_lines:
    base_row = "".join(c.char for c in base.cells[y])
    curr_row = "".join(c.char for c in current.cells[y])
    rec_row = "".join(c.char for c in reconstructed.cells[y])
    print(f"Line {y}: base={base_row!r} curr={curr_row!r} rec={rec_row!r}")
```

### TuiObservationBuffer läuft über

Der Buffer verwendet `maxlen` — älteste Snapshots werden automatisch verdrängt.
Konfiguriere `max_snapshots` entsprechend der Analyse-Anforderungen:

```python
from agent.services.heuristic_runtime.tui_observation_buffer import TuiObservationBuffer
buf = TuiObservationBuffer(max_snapshots=50, max_deltas=200)
```
