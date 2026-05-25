# Ananta Capoeira Terminal Duel

**Status:** neues Mini-Spiel-/Prototyp-Szenario fuer Ananta  
**Zweck:** extrem kleiner 3D-Terminal-/ASCII-Capoeira-Duell-Prototyp als kontrolliertes Entwicklungsbeispiel  
**Technikziel:** zuerst Terminal/TUI mit 3D-Projektion, spaeter optional echter 3D-Renderer  
**Scope-Regel:** erst 3D-Terminal-Spielgefuehl beweisen, dann Engine/Assets/VR.

## 1. Grundidee

`Ananta Capoeira Terminal Duel` greift die bestehende 3D-Terminal-/ASCII-Idee auf. Es ist kein Godot-First-Projekt. Der erste Prototyp soll im Terminal laufen und eine kleine 3D-Roda mit ASCII-/Unicode-Figuren darstellen.

Die Idee ist: Street-Fighter-artiges Duell, aber als stilisierte Terminal-3D-Simulation mit Capoeira-Bewegungen.

```text
Nicht: Godot-Game, Asset-Pipeline, VR, komplexe Engine.
Sondern: Terminal-3D-Roda + Ginga + Distanz + Esquiva + Kick.
```

## 2. Brutal-MVP

| Bereich | Entscheidung |
| --- | --- |
| Darstellung | Terminal/TUI mit ASCII/Unicode |
| 3D | einfache eigene 3D-zu-2D-Projektion |
| Spieler | 1 Spieler gegen Dummy |
| Arena | kleine runde Roda als projizierter Kreis/Bodenraster |
| Kamera | feste isometrische oder seitlich erhoehte Terminal-Kamera |
| Figuren | ASCII-/Unicode-Skelett oder einfache Glyph-Silhouette |
| Animation | Frame-basierte ASCII-Keyframes |
| Moves | Ginga, ein Kick, eine Esquiva |
| Kampf | deterministische Distanz-/Winkel-/Hitbox-Regeln |
| Ziel | pruefen, ob Capoeira-Bewegung im Terminal lesbar und spielbar wird |

## 3. Bewusst nicht im ersten Schritt

- Godot/Unity/Unreal
- echte 3D-Modelle
- Motion Capture
- VR/MR/Meta Quest
- Online-Multiplayer
- KI-Gegner mit Taktik
- Story/Kampagne
- komplexe Combos
- realistische Physik
- Asset-Polish
- Musik-/Rhythmus-System als Pflichtmechanik

Diese Themen bleiben geparkt, bis der Terminal-Core funktioniert.

## 4. Terminal-3D-Core

```mermaid
flowchart TD
    W[World 3D Coordinates] --> C[Camera Transform]
    C --> P[Perspective/Isometric Projection]
    P --> Z[Depth Sort / Z Buffer Light]
    Z --> R[Terminal Raster]
    R --> G[Glyph Renderer]
    G --> F[Frame Output]
```

Wichtig: Der Renderer muss klein bleiben. Kein vollstaendiger 3D-Engine-Nachbau. Es reicht ein stabiler Mini-Renderer fuer Punkte, Linien, Kreise, einfache Figuren und Hitbox-Debug.

## 5. Kern-Loop

```mermaid
flowchart TD
    A[Start in Ginga] --> B[3D-Position und Distanz lesen]
    B --> C{Dummy / Gegner im Kick-Winkel?}
    C -->|ja| D[Kick ausfuehren]
    C -->|nein| E[Position durch Ginga/Step anpassen]
    D --> F{Trefferfenster aktiv und Distanz passt?}
    F -->|ja| G[Punkt / Hit Marker]
    F -->|nein| H[Recovery]
    E --> I{Angriff erwartet?}
    I -->|ja| J[Esquiva]
    I -->|nein| A
    G --> A
    H --> A
    J --> A
```

## 6. Minimaler Move-Satz

```mermaid
flowchart LR
    G[Ginga Frames] --> S[Step / Position]
    G --> K[Kick Frames]
    G --> E[Esquiva Frames]
    K --> R[Recovery]
    E --> C[Counter Window spaeter]
    R --> G
    C --> G
```

| Move | Rolle im MVP |
| --- | --- |
| Ginga | animierter Grundrhythmus, leichte Positionsverschiebung |
| Kick | erster lesbarer Angriff, z. B. Martelo oder Meia Lua stark vereinfacht |
| Esquiva | Ausweichen durch Pose-/Hurtbox-Aenderung |

## 7. Technische Zielarchitektur

```mermaid
flowchart LR
    Input[Keyboard Input] --> GameLoop[Fixed Tick GameLoop]
    GameLoop --> State[GameState]
    State --> Moves[MoveStateMachine]
    Moves --> Combat[Combat Rules]
    State --> Scene[3D Scene Model]
    Combat --> Score[ScoreState]
    Combat --> Debug[Hitbox Debug Layer]
    Scene --> Projector[3D Projector]
    Projector --> Terminal[Terminal Renderer]
    Score --> Hud[ASCII HUD]
    Debug --> Terminal
    Hud --> Terminal
```

Kampf und Rendering bleiben getrennt. Der Renderer zeigt nur den Zustand. Die Regeln entscheiden deterministisch.

## 8. Vorgeschlagene Python-Struktur

```text
prototypes/ananta-capoeira-terminal-duel/
  README.md
  pyproject.toml
  src/ananta_capoeira_terminal_duel/
    main.py
    game_loop.py
    input.py
    state.py
    moves.py
    combat.py
    projection.py
    renderer.py
    glyphs.py
    hud.py
    action_log.py
  tests/
    test_projection.py
    test_move_state_machine.py
    test_combat_rules.py
    test_renderer_smoke.py
```

Moegliche Libraries, aber optional:

- `rich` fuer Terminal-Ausgabe,
- `textual` spaeter fuer TUI,
- zuerst notfalls plain ANSI.

## 9. Ananta-Integration als Entwicklungsbeispiel

```mermaid
sequenceDiagram
    participant U as User
    participant H as Ananta Hub
    participant P as Planner
    participant R as Renderer Worker
    participant G as GameCore Worker
    participant T as Test Worker
    participant V as Reviewer

    U->>H: Terminal-3D Capoeira Duel entwickeln
    H->>P: Scope auf Terminal-Brutal-MVP reduzieren
    P-->>H: Tasks + Artefakte + Nicht-Ziele
    H->>R: 3D-Projektion + ASCII-Renderer
    H->>G: GameState + Moves + Combat Rules
    H->>T: Tests fuer Projektion, StateMachine, Combat
    R-->>H: Renderer-Artefakte
    G-->>H: GameCore-Artefakte
    T-->>H: Testbericht
    H->>V: Review Gate
    V-->>H: Freigabe oder Rework
```

## 10. Erfolgskriterien fuer den ersten Prototyp

Der erste MVP ist erfolgreich, wenn:

- ein Terminal-Fenster eine kleine 3D-Roda zeigt,
- 3D-Punkte/Objekte stabil in 2D-Terminalkoordinaten projiziert werden,
- eine Fighter-Glyph-Figur sichtbar und steuerbar ist,
- Ginga als einfache Frame-Animation sichtbar ist,
- ein Kick als Animation/Hitbox-Debug sichtbar ist,
- eine Esquiva als Pose-/Hurtbox-Aenderung sichtbar ist,
- Treffer deterministisch nach Distanz/Winkel/Fenster erkannt werden,
- Punkte/HUD im Terminal angezeigt werden,
- Tests fuer Projektion, MoveState und Combat existieren.

## 11. Leitregel

Bis der Terminal-Prototyp Spass macht:

> Keine Engine-Flucht.

Erlaubt sind:

- bessere ASCII-Lesbarkeit,
- stabilere Projektion,
- bessere Keyframes,
- klarere Hitbox-Debug-Ausgabe,
- Tests ergaenzen,
- schlechte Moves streichen.

Nicht erlaubt:

- Godot-Umstieg als Ausrede,
- neue Charaktere,
- Online,
- Story,
- VR/MR,
- grosses Asset-System.
