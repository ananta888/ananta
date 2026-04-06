# Ollama-Modellrouting fuer Hub, Scrum-Rollen und OpenCode-Worker

Diese Uebersicht beschreibt die **aktuell im Projekt sichtbaren Ollama-Modelle** und leitet daraus eine sinnvolle Nutzung fuer den Ananta-Hub, Scrum-nahe Rollen, Templates und OpenCode-Worker ab.

## Projektregeln

- **Hub / zentrale Planung:** kleines bis mittleres Reasoning-/Planning-Modell fuer Goal->Plan->Task, Routing und Triage.
- **OpenCode-Worker:** bevorzugt explizite **Coder-Modelle** mit guter Tool-/Shell-Eignung.
- **Review / QA / Architektur:** Reasoning- oder staerkere Generalisten.
- **Templates / Doku / Prompt-Kaskaden:** kleine Generalisten statt schwerer Coding-Modelle.
- **Wichtige harte Beobachtung aus dem Live-Stack:** `ananta-default:latest` ist aktuell **keine gute Wahl fuer tool-basierte OpenCode-Worker**, weil der Live-Click-Pfad genau dort bereits mit *"does not support tools"* aufgefallen ist.

## Empfohlene Standardzuordnung

| Bereich | Empfehlung |
|---|---|
| `default_provider` | `ollama` |
| Hub-Copilot / Planung | `lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest` falls verfuegbar, sonst `phi-4-mini-reasoning` oder `glm-4-9b-0414` |
| Coding-Worker (OpenCode) | zuerst `qwen2.5-coder-14b`, dann `gpt-oss-20b-coder`, dann `qwen2.5-coder-7b` |
| Review / Analyse / QA | `glm-4-9b-0414`, `phi-4-mini-reasoning`, `llama-3.1-8b` |
| Doku / Template-Texte | `ministral-3-3b`, `qwen2.5-0.5b-instruct`, `gemma-4-e2b-it` |
| Vermeiden fuer OpenCode-Worker | `ananta-default`, `mmproj`-Modelle, `voxtral`-Audio/Multimodal-Modelle |

## Empfohlene Mapping-Logik fuer Rollen und Task-Kinds

| Ziel | Modellklasse |
|---|---|
| Scrum Master / Product Owner / Planner | leichtes Planning-/Reasoning-Modell |
| Architect / Reviewer / QA | Review-/Reasoning-Modell |
| Backend Developer / Frontend Developer / DevOps Engineer | Coder-Modell |
| `task_kind=planning` | Planning-Modell |
| `task_kind=analysis`, `review`, `research` | Review-/Reasoning-Modell |
| `task_kind=coding`, `testing` | Coder-Modell |
| `task_kind=documentation` | kleiner Generalist / Doku-Modell |

## Prompt-Kaskade fuer OpenCode/Shell/Terminal

1. **Hub plant und zerlegt** mit kleinem Planning-Modell.
2. **Template oder Rolle verfeinert** Sprache, Akzeptanzkriterien und Artefakterwartung.
3. **OpenCode-Worker fuehrt aus** nur mit tool-faehigem Coding-Modell.
4. **Review-/QA-Rolle bewertet** mit separatem Analyse-/Reasoning-Modell.

Faustregel:

- **OpenCode oder direkte Shell-Nutzung:** Coder-Modell
- **Reine Struktur-/Planungsarbeit:** kleines Planning-Modell
- **Review, Diff-Bewertung, Fehlerhypothesen:** Reasoning-/Review-Modell
- **Templates, Scrum-Texte, Dokumentation:** kleiner Generalist

## Aktuell sichtbare Modelle

### Planning / Reasoning / Review

| Modell | Empfohlene Nutzung | OpenCode-Worker |
|---|---|---|
| `lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest` | Hub-Planung, Routing, schnelle Triage | bedingt |
| `mradermacher-lfm2.5-1.2b-glm-4.7-flash-thinking-i1-gguf-lfm2.5-1.2b-c7d4a41ae661:latest` | wie oben, falls diese Variante stabiler laeuft | bedingt |
| `lmstudio-community-phi-4-mini-reasoning-gguf-phi-4-mini-reasoning-q4_k_m:latest` | Review, Analyse, Architekturabgleich | bedingt |
| `matrixportalx-glm-4-9b-0414-q4_k_m-gguf-glm-4-9b-0414-q4_k_m:latest` | Review, Analyse, QA | bedingt |
| `irmma-glm-z1-9b-0414-q4_k_s-gguf-glm-z1-9b-0414-q4_k_s-imat:latest` | tieferes Reasoning, Triage | bedingt |
| `lmstudio-community-phi-3.1-mini-128k-instruct-gguf-phi-3.1-mini-128-6739e0a9cd0e:latest` | leichte Analyse, Summaries, Langkontext-Fallback | bedingt |

### Coding / OpenCode-Worker

| Modell | Empfohlene Nutzung | OpenCode-Worker |
|---|---|---|
| `lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2:latest` | primaerer OpenCode-Worker fuer Code + Tool-Aufrufe | ja |
| `bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest` | leichterer Coding-Worker | ja |
| `mradermacher-qwen2.5-coder-3b-instruct-distill-qwen3-coder-next-abl-0836a1d595c6:latest` | guenstige Coding-Hilfe, kurze Tasks | bedingt |
| `lmstudio-community-qwen2.5-coder-0.5b-instruct-gguf-qwen2.5-coder-0-8a0ee15fcff4:latest` | schnelle Drafts / Kleinstaufgaben | bedingt |
| `tensorblock-deepseek-coder-v2-lite-instruct-gguf-deepseek-coder-v2-443776354e4e:latest` | Coding-Fallback, nur wenn stabil | bedingt |
| `davidau-qwen3-zero-coder-reasoning-0.8b-neo-ex-gguf-qwen3-zero-code-8221ab563223:latest` | experimenteller Hybrid aus Coding + Reasoning | bedingt |
| `davidau-openai_gpt-oss-20b-coder-neo-code-di-matrix-gguf-openai-20b-1eb340de9132:latest` | starker, aber schwerer Coding-Fallback | ja |

### Allgemeine Text-/Doku-/Template-Modelle

| Modell | Empfohlene Nutzung | OpenCode-Worker |
|---|---|---|
| `lmstudio-community-ministral-3-3b-instruct-2512-gguf-ministral-3-3b-c1858150c1d6:latest` | Doku, Templates, Prompt-Ausarbeitung | bedingt |
| `brouzuf-mistral-7b-instruct-v0.3-q4_k_m-gguf-mistral-7b-instruct-v0-fe52427380fa:latest` | allgemeine Assistenz, Doku, Review-Fallback | bedingt |
| `ddarolf-meta-llama-3.1-8b-instruct-q4_k_m-gguf-meta-llama-3.1-8b-instruct-q4_k_m:latest` | Review-/Generalist-Fallback | bedingt |
| `bylang-meta-lama3.1-8b-q4_k_m-gguf-meta-lama3.1-8b-q4_k_m:latest` | Generalist-Fallback | bedingt |
| `lmstudio-community-qwen2.5-0.5b-instruct-gguf-qwen2.5-0.5b-instruct-q8_0:latest` | schnelle Templates, kurze Texte | bedingt |
| `mradermacher-openai-7b-v0.1-gguf-openai-7b-v0.1.q4_k_s:latest` | allgemeine Assistenz | bedingt |
| `lmstudio-community-gpt-oss-20b-gguf-gpt-oss-20b-mxfp4:latest` | schwerer Generalist / Backup | ja |

### Gemma / multimodal / Spezialfaelle

| Modell | Empfohlene Nutzung | OpenCode-Worker |
|---|---|---|
| `lmstudio-community-gemma-4-e2b-it-gguf-gemma-4-e2b-it-q4_k_m:latest` | Template-/Text-Assistenz | bedingt |
| `lmstudio-community-gemma-4-e2b-it-gguf-mmproj-gemma-4-e2b-it-bf16:latest` | multimodale Experimente | nein |
| `bartowski-mistralai_voxtral-mini-3b-2507-gguf-mistralai_voxtral-min-9e08d0b2625f:latest` | Audio-/Realtime-nahe Spezialfaelle | nein |
| `bartowski-mistralai_voxtral-mini-3b-2507-gguf-mmproj-mistralai_voxt-2efae94b6ce9:latest` | multimodale Audio-/Vision-Pfade | nein |
| `andrijdavid-voxtral-mini-4b-realtime-2602-gguf-q2_k:latest` | Realtime-/Audio-Experimente | nein |

### Projektinterne Modelle

| Modell | Empfohlene Nutzung | OpenCode-Worker |
|---|---|---|
| `ananta-default:latest` | Alias auf `bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest`; standardmaessiger Coding-/Worker-Default | ja |
| `ananta-smoke:latest` | Alias auf ein kleines/schnelles Fallback-Coder-Modell (bevorzugt `qwen2.5-coder-3b`, sonst kleiner); fuer Smoke-/Health-/Live-Tests | bedingt |

## Konkrete Konfigurationsidee fuer Ananta

```json
{
  "default_provider": "ollama",
  "default_model": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
  "hub_copilot": {
    "enabled": true,
    "provider": "ollama",
    "model": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
    "strategy_mode": "planning_and_routing"
  },
  "task_kind_model_overrides": {
    "planning": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
    "analysis": "matrixportalx-glm-4-9b-0414-q4_k_m-gguf-glm-4-9b-0414-q4_k_m:latest",
    "review": "matrixportalx-glm-4-9b-0414-q4_k_m-gguf-glm-4-9b-0414-q4_k_m:latest",
    "coding": "lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2:latest",
    "testing": "lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2:latest",
    "documentation": "lmstudio-community-ministral-3-3b-instruct-2512-gguf-ministral-3-3b-instruct-2512-q4_k_m:latest"
  }
}
```

## Hinweis zu Benchmarkdaten

Die vorhandenen Projekt-Benchmarkdateien zeigen aktuell nur fuer einen Teil der Modelle belastbare Daten. Deshalb ist die Zuordnung hier **absichtlich operational**:

- Live-Stack-Beobachtung fuer Tool-Support
- Modellfamilie und Eignung fuer Coding/Reasoning
- vorhandene Projekt-Historie

Die Zuordnung sollte nach weiteren Live-Click- und Worker-Benchmarklaeufen weiter verfeinert werden.
