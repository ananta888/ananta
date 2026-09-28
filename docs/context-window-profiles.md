# Kontextfenster-Profile

Das Kontextfenster pro Anfrage ist **eine zentrale Einstellung** (`agent/context_profile.py`). Alle
Budgets, die davon abhängen, werden daraus abgeleitet: Kontext-Bündel, CodeCompass-Planner und
Agentic Retrieval, RLM, Hybrid-RAG, Worker-Tool-/Mutations-Loops, Planung, Recovery, Compactor,
Langkontext-Zerlegung und die Token-Guardrail. Keine Komponente nimmt mehr selbst „32k“ an.

```text
Konfiguriertes Fenster (Profil / Tokens)
          |
Provider-/Modell-Limit (llama.cpp /props, LM Studio /v1/models, Ollama /api/show, llm_config)
          |
Effektives Fenster = kleinstes bekanntes Limit, nie mehr als konfiguriert
          |
ContextBudgets (Anteile des effektiven Fensters)
   Retrieval / Evidence / Bündel / RLM      Tool-Ergebnisse / Planung / Recovery
   Ausgabe-Reserve                          Sicherheitsreserve, fester Anfrage-Anteil
```

## Profile

| Profil | Tokens | Wann |
|---|---|---|
| `compact_12k` | 12 288 | kleine Modelle |
| `standard_32k` | 32 768 | **Standard, empfohlen** für lokale Modelle |
| `full_64k` | 65 536 | Modell/Laufzeit liefert 64k pro Anfrage |
| `extended_128k` | 131 072 | nur wenn Modell und Laufzeit wirklich 128k liefern |
| `custom` | beliebig (2 048 … 1 048 576) | eigenes Fenster |

## Einstellen

```env
ANANTA_CONTEXT_PROFILE=standard_32k
ANANTA_CONTEXT_PROFILE=full_64k
ANANTA_CONTEXT_PROFILE=extended_128k
# Custom: ein explizit gesetzter Wert gewinnt über das Profil
ANANTA_CONTEXT_TOKENS=49152
```

Zur Laufzeit (Einstellungen → LLM → „Kontextfenster“, oder API):

```http
POST /config  {"context_window": {"profile": "full_64k"}}
POST /config  {"context_window": {"profile": "custom", "tokens": 49152}}
POST /config  {"context_window": {}}          # zurück zur Umgebung
GET  /config/context-window                   # konfiguriert, erkannt, effektiv, Budgets
```

Reihenfolge: Laufzeit-Tokens (custom) → Laufzeit-Profil → `ANANTA_CONTEXT_TOKENS` (nur wenn explizit
gesetzt) → `ANANTA_CONTEXT_PROFILE` → `standard_32k`. Bestehende Installationen mit
`ANANTA_CONTEXT_TOKENS=32768` laufen unverändert.

Die Laufzeit muss das Fenster auch liefern: llama-server `-c <Fenster × parallele Slots>`, Ollama
`num_ctx`, LM Studio Context Length. Liefert sie weniger, gilt automatisch ihr Wert: Profil 128k auf einem
32k-Modell ergibt effektiv 32k und 32k-Budgets.

## Effektives Fenster

Das kleinste bekannte Limit aus:

- konfiguriertes Fenster,
- `llm_config.context_limit` für seinen Provider (der alte, aus 32768 befüllte Standardwert zählt nicht),
- Modell-Tabelle `LMSTUDIO_MODEL_CONTEXTS` (z. B. phi-3.5-mini 4096),
- was der Provider meldet: llama.cpp `/props` (`n_ctx`), LM Studio (`loaded_context_length`/`context_length`),
  Ollama `/api/show` (`*.context_length`), 5 Minuten gecacht, schlägt nie fehl,
- Laufzeit-/Backend-Limits (opencode 128 000, `MAX_PROMPT_TOKENS`, `LMSTUDIO_MAX_CONTEXT_TOKENS`).

`ANANTA_CONTEXT_PROVIDER_PROBE=0` schaltet die Abfrage ab.

## Nur für lokale Runtimes

Das Profil beschreibt das Fenster, auf das Ananta seine Prompts für **lokale Modelle** zuschneidet (Ollama,
LM Studio, llama.cpp, konfigurierte `local_openai_backends`). **Abo-/Cloud-Modelle begrenzt es nie:**

| Pfad | Grenze |
|---|---|
| `generate_text` / Strategien mit Cloud-Provider (openai, anthropic, …) | eigenes Fenster des Providers; nur ein explizites oder für den Provider deklariertes Limit |
| claude-cli (`run_claude_command`, schreibender Claude-Run) | Modellgrenze 200 000 |
| codex-cli | Modellgrenze 272 000 |
| opencode mit Cloud-Modell (`anthropic/…`, `openai/…`, `gemini/…`, …; auch als konfiguriertes Standardmodell) | Modellgrenze (Claude 200 000, sonst 128 000) |
| opencode mit lokalem Modell, sgpt | effektives Ananta-Fenster (opencode höchstens 128 000) |

`MAX_PROMPT_TOKENS` übersteuert alle CLI-Gates bewusst. (`agent.context_profile.window_for_provider`,
`agent/cli_backends/budget.prompt_token_limit`.) Ein Codex/opencode/aider, der auf eine lokale Runtime zeigt
(`inference_target_kind: local_openai`), gilt als lokal.

**Langkontext-Zerlegung:** Der Hub sagt vor dem Zerlegen voraus, welche Runtime den Task ausführt – mit
denselben Auflösern wie der Worker beim Propose (`resolve_task_cli_backend` über Hub-Config plus Goal-Snapshot,
dann `routing_dimensions`; `agent/services/task_execution_window_service.py`) – und misst den Task am Fenster
dieser Runtime: ein 84k-Task für claude-cli (200k) wird nicht zerlegt, derselbe Task für das lokale Modell schon.
Entscheidet ein Worker anders und läuft über, zerlegt die Überlauf-Behandlung (LCTX-009) nach dem lokalen Fenster.

## Budget-Policy

Vom effektiven Fenster `W` gehen ab:

- **Ausgabe-Reserve** `W/16`, 1 024 … 8 192 (32k: 2 048),
- **Sicherheitsreserve** für Schätzfehler `W/16`, mindestens 512 (Tokenizer-Messung: Python 4,3, deutsche Doku
  3,9, TypeScript 3,8, JSON 3,4 Zeichen/Token – die 4-Zeichen-Schätzung liegt bei Text/Code bis ~6 % zu niedrig,
  bei reinem JSON ~15 %),
- **fester Anfrage-Anteil** (System-Prompt, Tool-Definitionen, AGENTS.md, Task) `context_strategy.request_overhead_tokens`,
  Standard 12 000, höchstens 40 % des Fensters.

Der Rest ist **verfügbar** für Material. Jedes Budget ist ein Anteil von `W` und höchstens „verfügbar“ – so
kann keine Komponente das Fenster füllen, und Reserven + fester Anteil + größtes Budget ≤ `W`
(`ContextBudgets.validate`). Die Anteile sind bei 32k kalibriert: dort ergeben sie genau die früheren
festen Werte.

| Budget | Anteil | 32k | 64k | 128k |
|---|---|---|---|---|
| verfügbar für Material | – | 16 672 | 45 344 | 102 688 |
| Bündel compact / standard / full | 12,5 / 37,5 / 50 % | 4 096 / 12 288 / 16 384 | 8 192 / 24 576 / 32 768 | 16 384 / 49 152 / 65 536 |
| Evidence (CodeCompass-Planner, Retrieval-Obergrenze, RLM-Synthese) | 45 % | 14 745 | 29 491 | 58 982 |
| Hybrid-RAG-Kontext | ≈ 9 % | 3 000 | 6 000 | 12 000 |
| ein Tool-Ergebnis / alle Tool-Ergebnisse | ≈ 6 % / 50 % | 2 000 / 16 384 | 4 000 / 32 768 | 8 000 / 65 536 |
| Diff (Mutation) / Compactor-Ausgabe | ≈ 9 % | 3 000 | 6 000 | 12 000 |
| Planungssegment / Planungskontext | ≈ 6 % / 18 % | 2 000 / 6 000 | 4 000 / 12 000 | 8 000 / 24 000 |
| Recovery-Kontext | 25 % | 8 192 | 16 384 | 32 768 |
| Chat-Kontext (`chat_context_chars`, lokale Chat-Backends) / Pre-Model-Kontext | ≈ 9 % | 3 000 | 6 000 | 12 000 |
| Visual-Process-Editor: selected / conversation | 12,5 % / ≈ 37 % | 4 096 / 12 000 | 8 192 / 24 000 | 16 384 / 48 000 |
| Context-Curation | ≈ 31 % | 10 000 | 20 000 | 40 000 |
| Worker-Batch-Loop: Datei-Ausschnitt / Snippet | ≈ 3 % / 6 % | 1 000 / 2 000 | 2 000 / 4 000 | 4 000 / 8 000 |
| Snake-RAG: Komponenten-Katalog / ein gelesener Datei-Inhalt | ≈ 15 % | 5 000 | 10 000 | 20 000 |

(Tokens; Zeichen = Tokens × 4.)

**Explizite Werte** (z. B. `ananta_worker_tool_loop.max_tool_result_chars`, `planning_policy.segment_context_chars`,
Bündel-Budgets, `codecompass_context_tools.max_tokens_*`, `RAG_MAX_CONTEXT_TOKENS`) bleiben Overrides, werden
aber auf „verfügbar“ begrenzt. Gespeicherte **historische Standardwerte** (8000, 12000, 12288, …) zählen als
„nicht gesetzt“ – sonst würde eine bestehende Installation bei 64k nicht mitwachsen. Hardware-Profile mit
kleineren Werten (Laptop 1400 Zeichen Planungssegment) gelten weiter.

Weitere Stellen:

- **Chat:** gilt nur für lokale Chat-Backends (`ananta-worker`, `sgpt`, Ollama, LM Studio, llama.cpp); ein
  Abo-/Cloud-Chat-Backend behält seinen Wert (Standard 12 000 Zeichen) und wird nicht begrenzt. Die TUI hält
  eigene Einstellungen; der Hub bewertet sie mit `effective_chat_context_chars`.
- **Pi:** bekommt für lokale Runtimes das effektive Fenster als `contextWindow`, für entfernte Modelle weiterhin
  sein eigenes 8 192-Limit; das Hub-Budget (`max_total_tokens`) begrenzt jeden Aufruf zusätzlich.
- **Laufzeit-Empfehlung / `ananta init`:** empfehlen die Profile (32 768 / 65 536) samt Bündel-Budget statt
  32 000 / 64 000; das Etikett kommt aus `nearest_profile` (inkl. `extended_128k`).

## Echte Sonderlimits (bleiben fest)

Werte, die nicht vom Modellfenster abhängen: Anzahl Chunks/Bereiche/Schritte (`max_chunks`, `max_ranges`,
RLM `max_depth`/`max_fanout`/`max_steps`, `MAX_STEPS`), Zeilen je Bereich, Excerpt-/Vorschau-Längen,
Zwischenstand-Länge der Langkontext-Schritte (600 Wörter), Task-Brief-/Hub-Kontext-/Research-Prompt-Bausteine
des interaktiven opencode-Profils (900 / 2 600 / 1 800 Zeichen – bewusst knappe Arbeitsdateien für ein CLI mit
eigenem Fenster), `chat_max_tokens` (Antwortlänge, eine Produktentscheidung), Byte-Grenzen (`max_total_bytes`),
Timeouts, die bewusst konservativen `max_input_tokens` der Hardware-Empfehlungen.

## Messung: derselbe Task unter 32k / 64k / 128k

`scripts/long_context_e2e.py --cases ordered --context-profile <profil>` (im Hub-Container; Profil und
Strategie-Modus werden nach dem Lauf zurückgesetzt). Aufgabe: 84 127 Token Ananta-Doku zu einer
Architekturübersicht in 20 Punkten zusammenfassen. eGPU-Standardmodell (Bonsai 2 27B, llama.cpp
`-c 65536 -np 2`, meldet `n_ctx` 65 536), Autopilot `safe`, 2026-09-28.

| | standard_32k | full_64k | extended_128k |
|---|---|---|---|
| effektives Fenster | 32 768 (konfiguriert) | 65 536 (konfiguriert) | **65 536 (Provider-Limit)** |
| verfügbar für Material (damalige Reserve W/20) | 17 082 | 46 164 | 46 164 |
| Strategie / Schritte | sequential, 9 + 1 | sequential, 4 + 1 | sequential, 4 + 1 |
| Modellaufrufe | 10 | 5 | 5 |
| größter Prompt (Token) | 30 460 | 45 968 | 46 596 |
| Laufzeit | 25,4 min | 10,9 min | 12,1 min |
| Kürzungen (`context_truncation_total`) | 0 | 0 | 0 |
| Ergebnis | vollständig, 10 833 Zeichen | vollständig, 5 464 Zeichen | vollständig, 3 941 Zeichen |

Material für Retrieval fällt bei diesem Fall nicht an (das Material steckt im Task selbst); die
„Evidence Coverage“ des Skripts zählt nur, welche Quelldateinamen in der Zusammenfassung stehen (32k: 23 %,
64k/128k: 5 % bzw. 0 %) und ist für eine 20-Punkte-Übersicht kaum aussagekräftig – alle drei Ergebnisse sind
inhaltlich brauchbar, die 32k-Fassung ist ausführlicher, weil mehr Zwischenstände einfließen.

Ergebnis: doppeltes Fenster → halb so viele Schritte und Modellaufrufe, 43 % der Laufzeit; jede Anfrage
bleibt unter ihrem Fenster. Das 128k-Profil auf einer 64k-Laufzeit arbeitet automatisch mit 64k.
