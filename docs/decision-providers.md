# Decision-Provider: TypeSafe Jev, lokaler Jev-Modus, LLM, Regeln

**Track:** DPRV · **Stand:** 2026-09-27
**SOLID-Bezug:** ISP (kleiner Port `DecisionProvider`), DIP (Transport und LLM als Ports), OCP (neue
Provider und Einsatzbereiche über Config, nicht über Code-Zweige), SRP (Provider, Kaskade, Service,
Config, Auswertung getrennt).

Jev ist kein Chat-Modell: Es beantwortet **eine typisierte Frage** über einen Text – eine Auswahl
(`choice`), eine Bewertung auf einer Skala (`score`) oder eine Ja/Nein-Wahrscheinlichkeit (`noul`) – und
liefert Wahrscheinlichkeiten statt Prosa. Dafür gibt es in Ananta jetzt eine allgemeine
Decision-Provider-Schicht, an die Jev, der lokale llama.cpp-Entscheidungsmodus, ein LLM und Regeln
gleichberechtigt angeschlossen sind.

## Architektur

```
agent/services/decision_providers/
├── types.py        DecisionQuestion/Request/Answer/Result, peak_confidence, DecisionProviderError
├── base.py         DecisionProvider (Port) + adecide (async)
├── typesafe.py     TypeSafe Jev  (POST https://api.typesafe.ai/v1/systemone)
├── llamacpp.py     lokaler Jev-Modus (POST /v1/decision, llama.cpp-Fork)
├── llm.py          Chat-LLM beantwortet dieselben Fragen als JSON (System 2)
├── simple.py       Regeln, feste Antworten (Tests, Baseline)
├── cascade.py      Regeln → Jev → LLM → deferred, mit Schwellen pro Stufe/Frage
├── config.py       Config-Abschnitt decision_providers (Validierung, Defaults)
├── service.py      Einsatzbereiche zur Laufzeit: off / shadow / active, Aufzeichnung
├── tool_choice.py  Tool-Auswahl als Fragen (gemeinsam für Router und Benchmark)
└── evaluation.py   Benchmark-Metriken (nutzt die Tiny-Router-Kalibrierung)
agent/services/tiny_router/decision_provider_adapter.py   Anbindung an den Tiny-Tool-Router
scripts/decision_provider_benchmark.py                    reproduzierbarer Vergleich
benchmarks/decision_providers/*.v1.json                   gelabelte Entscheidungsfälle
```

- **Vorschlag, keine Freigabe.** Eine Entscheidung ist ein Vorschlag. Risk-Filter, Kandidaten-Validator,
  Policy-, Approval-, HITL- und Mutation-Gates bleiben unverändert davor bzw. danach; die Schicht kann nichts
  freigeben.
- **Deferral statt Raten.** Erreicht keine Stufe ihre Schwelle, ist das Ergebnis `deferred`, und der
  Aufrufer bleibt auf seinem heutigen Weg (Regeln, Haupt-LLM, Reviewer/Mensch).
- **Eine Deadline** für die ganze Kaskade; TypeSafe mit Retry (429/529/5xx/Timeout, `retry-after-ms`,
  ein `Idempotency-Key`), 401/403/422 werden nie wiederholt.
- **Strikte Antwortprüfung:** nur gefragte Schlüssel und Typen, nur bekannte Labels, endliche
  Wahrscheinlichkeiten – sonst `response_invalid`.
- **Confidence** ist über alle Provider vergleichbar: TypeSafes Statistik `(n·peak − 1)/(n − 1)`, bei Noul
  `|2p − 1|`. Beim LLM ist sie Selbstauskunft und **nicht kalibriert**.

## Konfiguration

Hub-Config-Abschnitt `decision_providers` (`POST /config`, validiert in
`agent/routes/config/settings.py`; Defaults in `agent/config_defaults.py`). **Alles ist standardmäßig aus.**

```json
{
  "decision_providers": {
    "enabled": true,
    "confidence_threshold": 0.9,
    "deadline_seconds": 8,
    "providers": {
      "jev": {"enabled": true, "external_calls_allowed": true, "model": "jev-latest",
              "api_key_env": "TYPESAFE_API_KEY", "timeout_seconds": 5, "max_retries": 2},
      "local_decision": {"enabled": true, "base_url_env": "ANANTA_PARALLEL_DECISION_URL"},
      "llm": {"enabled": false}
    },
    "areas": {
      "tool_routing": {"mode": "shadow", "cascade": ["jev"], "confidence_threshold": 0.9}
    }
  }
}
```

| Schlüssel | Bedeutung |
|---|---|
| `enabled` | globaler Schalter; `false` = Verhalten wie vor DPRV |
| `providers.jev.external_calls_allowed` | **Pflicht** für Jev: der Entscheidungstext verlässt die Maschine |
| `providers.jev.api_key_env` | Name der Env-Variable; `<NAME>_FILE` (Datei mit Key oder `NAME=…`-Zeile) geht auch. **Keys in der Config werden abgelehnt.** |
| `areas.<bereich>.mode` | `off` (heute), `shadow` (entscheiden + aufzeichnen, heutiges Verhalten gewinnt), `active` |
| `areas.<bereich>.provider` / `cascade` | ein Provider oder eine Kette, z. B. `["jev", "local_decision"]`; eine Liste in der Kette ist eine Übereinstimmungs-Gruppe, z. B. `[["local_decision", "jev"], "llm"]` |
| `areas.<bereich>.confidence_threshold` | Schwelle für diesen Bereich (sonst die globale) |
| `areas.<bereich>.question_thresholds` | strengere Schwelle für einzelne Fragen |

Bereiche: `tool_routing`, `companion_route`, `companion_knowledge`, `retrieval_intent`, `rag_needed`,
`chat_intent`, `hub_direct`, `prompt_injection`. Angebunden sind `tool_routing` und `retrieval_intent`; die anderen sind
konfigurierbar und gemessen (siehe Ergebnisse), aber nicht verdrahtet.

**Retrieval-Intent aktivieren:** `decision_providers.areas.retrieval_intent` auf `shadow` (nur aufzeichnen)
bzw. `active`. Die Regeln bleiben die Standardantwort; eine Entscheidung ersetzt sie nur, wenn eine Stufe sie
über der Schwelle akzeptiert. Ein vom Nutzer erzwungener Trigger-Modus und leere Anfragen werden nie übersteuert.

**Key für den Hub:** Der Hub-Container kennt `TYPESAFE_API_KEY` nicht; für Jev im Hub die Key-Datei als
Secret mounten und `TYPESAFE_API_KEY_FILE` setzen (oder `api_key_env` auf eine vorhandene Variable).

**Tool-Routing aktivieren:** Profil `decision-provider-cascade` im Tiny-Router
(`ananta_worker_tool_loop.tiny_router.profile_order`) und `decision_providers.areas.tool_routing` auf
`shadow` oder `active`. Ob ein Kandidat genutzt wird, entscheidet weiter der Modus des Tiny-Routers
(`disabled`/`shadow`/`active`, Kill-Switch); jede Abstention geht an das Haupt-LLM.

**Abschalten:** `decision_providers.enabled=false` oder der Bereich auf `off` – sofort, ohne Neustart.

## Secrets

- Key nur aus `TYPESAFE_API_KEY` oder `TYPESAFE_API_KEY_FILE` (bzw. dem konfigurierten `api_key_env`),
  gelesen zur Aufrufzeit; nie in Config, Ergebnis, Fehler, Log, Aufzeichnung oder `repr`.
- Nur über TLS (oder an einen lokalen kompatiblen Server auf 127.0.0.1/localhost).
- Aufzeichnungen enthalten den Entscheidungstext nicht, nur seinen SHA-256.

## Tests und Benchmark

```bash
# headless, ohne Netz/Key/Modell
cd docker/compose-next && docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm \
  --user 1000:1000 t-infra sh -c "python -m pytest -q tests/test_decision_providers.py tests/test_decision_provider_config.py"

# Benchmark (lokaler Server auf :18150; Jev nur mit Key)
TYPESAFE_API_KEY_FILE=~/.config/secrets/api-keys.env \
  python3 scripts/decision_provider_benchmark.py --out data/decision-benchmarks/report.json
#   --providers rules,current,chat,jev,local_decision,llm   --areas tool_routing,companion,...   --limit 3
```

## Benchmark 2026-09-27

`scripts/decision_provider_benchmark.py`, Report `data/decision-benchmarks/report-2026-09-27.json` (nicht
versioniert). Lokales Modell: Bonsai 27B auf der eGPU (`:18150`), TypeSafe `jev-latest` (Antwort `jev-1.13.0`).

**Daten.** Echte Nutzeräußerungen speichert Ananta bewusst nicht (Hub und Meet-Worker halten nur Metadaten),
Produktionsverkehr gab es also nicht. Gemessen wurden die echten Entscheidungsstellen mit ihren echten
Label-Mengen: je ein **Basis-Set** aus den Testfällen der heutigen Regeln (Label-Quelle `policy_rule` – die
Regeln schaffen dort per Konstruktion 100 %) und ein **Hard-Set** mit realistischen Formulierungen
(Umgangssprache, Tippfehler, Synonyme, Negation, Mehrfachabsicht, Injection) – synthetisch, **ein Annotator**,
nicht menschlich geprüft. Tool-Routing: die 119 vorhandenen gelabelten Fälle (`benchmarks/tiny_tool_router/`).
Alles ist Evaluationsmaterial, keine Release-Evidenz.

### Tool-Routing (119 Fälle, Companion-Tools)

| Provider | Acc | Fehler ≥ 0,9 | p50 / p95 | Abdeckung bei 0,8 / 0,9 (Präzision) |
|---|---|---|---|---|
| heute: lokaler `/v1/decision`-Pfad (inkl. Argument) | **1,000** | 0 | 757 / 1187 ms | 89 % / 79 % (100 %) |
| heute: Chat-Tool-Call (System 2) | 0,983 | 2 | 1198 / 2805 ms | – (keine Confidence) |
| lokaler Jev-Modus, generisch | 0,992 | 0 | 335 / 346 ms | 82 % / 68 % (100 %) |
| **TypeSafe Jev** | 0,950 | **0** | **300 / 380 ms** | 73 % / 63 % (100 %) |
| LLM als JSON-Entscheider | 0,882 | 6 (+8 ungültig) | 1384 / 1840 ms | – |

Kaskade Jev (≥ 0,8) → heutiger Pfad: 100 %, 27 % Fallback, Ø 550 ms. Jev wählt nur das Tool; das
Such-Argument ist der Prompt selbst, der lokale Pfad formuliert es.

### Entscheidungen der heutigen Regeln (Hard-Sets; Basis-Sets: Regeln 100 %)

| Bereich (Art) | n | Regeln | TypeSafe Jev | lokaler Jev-Modus | LLM | Jev ≥ 0,9 → LLM |
|---|---|---|---|---|---|---|
| Companion-Route (choice, 4) | 28 | 0,357 (18 sichere Fehler) | **0,964** | **0,964** | 0,893 (3) | 0,929, 29 % Fallback |
| Retrieval-Intent (choice, 9) | 28 | 0,393 (17) | 0,929 | **1,000** | 1,000 | **1,000**, 21 % |
| Hub-Direct-Tool (choice, 8) | 27 | 0,407 (16) | **1,000** | 0,963 | 0,963 (1) | 1,000, 22 % |
| Chat-Intent (choice, 5) | 26 | 0,500 (13) | **1,000** | 0,962 | 0,923 (2) | 0,962, 15 % |
| Wissen nachschlagen? (noul) | 27 | 0,593 (11) | 0,889 | **1,000** | 0,963 (1) | 0,963, 78 % |
| RAG nötig? (noul) | 27 | 0,481 (14) | 0,741 | **0,926** | 0,963 (1) | 0,963, 78 % |

Latenz p50: Jev 300–330 ms (Netz + TLS; Serverzeit ~95 ms), lokaler Jev-Modus 250–330 ms, LLM 1,1–1,4 s,
Regeln < 1 ms. Kosten Jev: 0,013–0,036 $ pro 1000 Entscheidungen (≈ 300–860 Input-Token je Anfrage; der ganze
Benchmark kostete 0,009 $). Lokal: keine Kosten pro Aufruf.

### Befunde

1. **Regeln** sind auf ihren eigenen Fällen perfekt, auf realistischen Formulierungen schwach – und sie wissen
   es nicht: jeder Fehler ist ein „sicherer“ Fehler. Das ist die Lücke, die ein kalibrierter Entscheider füllt.
2. **Jev bei `choice`**: 93–100 % auf den Hard-Sets, **null Fehler mit Confidence ≥ 0,9 in allen
   choice-Bereichen**, bei 0,8 ebenfalls null. Die Confidence trennt verlässlich; unsicher heißt meist
   mehrdeutig.
3. **Jev bei `noul`** (Ja/Nein) ist schwach: 74–89 %, und die Confidence bleibt niedrig (Abdeckung bei 0,9:
   22 %, bei 0,8 zwei Fehler). Ja/Nein-Fragen besser als Teil einer Auswahl stellen (RAG nötig ⇔ Intent ≠
   `generic_chat`) oder lokal entscheiden.
4. Der **lokale Jev-Modus** ist gleichauf oder besser (auch bei noul), gleich schnell, kostenlos, und die Daten
   bleiben lokal – wo die eGPU läuft, ist er die erste Wahl. Jev lohnt sich dort, wo kein lokaler
   Entscheidungsserver läuft (Worker/Hosts ohne GPU), und als zweite Stufe bei dessen Ausfall.
5. Das **LLM** ist als Rückfall gut, als alleiniger Entscheider nicht: es macht sichere Fehler (keine
   kalibrierte Confidence), ist 4× langsamer und liefert gelegentlich ungültige Antworten.
6. **Kaskaden-Reihenfolge**: Jev → LLM ist durchweg gut; **Jev → Regeln** verschlechtert die Hard-Sets (wo Jev
   unsicher ist, irren die Regeln meist auch). Regeln gehören **vor** den Entscheider (leere Eingaben,
   ausdrückliche Befehle/Modi), nicht dahinter.

### Empfehlung

```
Regeln (harte Fälle: leer, explizit erzwungen, Policy)
  ↓
lokaler Jev-Modus  ─ nicht verfügbar ─▶ TypeSafe Jev (Opt-in)
  ↓ Confidence < 0,9
bestehender Pfad (Haupt-LLM / heutiger Tool-Pfad)
  ↓
Reviewer / Mensch (bestehende Gates, unverändert)
```

| Bereich | Eignung | Stand | Schwelle |
|---|---|---|---|
| Tool-Routing | geeignet (choice); lokal besser, Jev als Ersatz/zweite Stufe | **angebunden** (Tiny-Router-Profil `decision-provider-cascade`) | 0,9 |
| Retrieval-Intent (inkl. RAG ja/nein) | sehr geeignet | **angebunden** (`retrieval_profile_service`), Start in `shadow` | 0,9 |
| Companion-Route | sehr geeignet | nicht angebunden: läuft im Meet-Worker, der den Hub-Code nicht importieren darf; nächster Schritt: Worker-Port auf den vorhandenen `/v1/decision`-Client | 0,9 |
| Chat-Intent (TUI) | geeignet | kein Produktions-Aufrufer | – |
| Hub-Direct | nur Tool-Wahl geeignet; Argumente (Pfad, Muster) kann Jev nicht liefern | nicht angebunden | – |
| Ja/Nein-Entscheidungen (noul) | mit Jev **nicht** geeignet | – | ≥ 0,95 oder lokal |
| Approval/HITL/Mutation-Gates | **nie** | unverändert | – |
| Worker-/Modellauswahl, Fallback-Policies | nichts zu klassifizieren (deterministisch) | – | – |

**Schwellen:** 0,9 als Standard (auf den Daten null sichere Fehler, 63–85 % Abdeckung bei choice); 0,8 gab
bei choice ebenfalls keine Fehler und mehr Abdeckung, sollte aber erst nach einem Shadow-Lauf mit echten
Anfragen gesetzt werden. 0,95/0,98 kosten viel Abdeckung ohne messbaren Gewinn. Grenzen: kleine Sets, ein
Annotator – Shadow-Aufzeichnungen (`ananta.decision_providers`-Log, ohne Text) liefern die Bestätigung.

## Übereinstimmung und Prompt-Injection (2026-09-27)

**Übereinstimmungs-Stufe.** Ein Eintrag der Kaskade kann eine Gruppe sein:
`"cascade": [["local_decision", "jev"], "llm"]`. Die Mitglieder antworten parallel (Latenz = das langsamste),
akzeptiert wird nur dieselbe Antwort aller Mitglieder, jede über der Schwelle; sonst `disagreement` bzw.
`low_confidence` und weiter zur nächsten Stufe. Fehlt ein Mitglied (kein Key, Server aus), ist die Gruppe
`unavailable` – sie wird nie stillschweigend zur Einzelentscheidung.

**Injection-Screening** (`agent/services/prompt_injection_screening.py`, Bereich `prompt_injection`): eine
Auswahl-Frage „was versucht dieser Text?“ – `benign`, `instruction_override`, `data_exfiltration`,
`command_execution`, `jailbreak_roleplay`. Ergebnis ist ein **Hinweis**, kein Gate: `benign`, `suspicious`
(sicher erkannter Angriff) oder `uncertain` (Widerspruch oder zu unsicher → prüfen). Mit einer Gruppe gilt ein
Text nur dann als harmlos, wenn beide Modelle das sicher sagen. Bestehende Pattern-Checks, Redaction und alle
Gates bleiben unverändert. Angebunden an zwei Eingänge: `/llm/generate` (Nutzer-Prompt) und die Goal-Planung (`planning_service`, nach `validate_goal`). `shadow`: läuft im Hintergrund (begrenzter Executor, verwirft bei Überlast statt zu stauen), keine zusätzliche Latenz, nur Aufzeichnung ohne Text. `active`: synchron; `/llm/generate` liefert den Hinweis additiv als `injection_screen` in den Metadaten, die Goal-Planung protokolliert ihn. In keinem Modus wird etwas blockiert.

Datensatz `benchmarks/decision_providers/prompt_injection.v1.json`: 67 Fälle DE/EN – direkte, indirekte (in
Ticket/README/Mail/Kommentar), verschleierte (Leetspeak, gesperrt, Base64) und Rollenspiel-Angriffe sowie
harmlos klingende Fragen, die gefährlich klingen; ein Annotator, synthetisch.

| Erkennung (Angriff ja/nein) | Recall | Fehlalarme | Typ richtig | sichere Fehler | p50 |
|---|---|---|---|---|---|
| heutige Pattern-Listen (vereint) | **0,27** | 0,13 | – | – | < 1 ms |
| TypeSafe Jev, Schwelle 0,9 | **1,00** | 0,17 (0,09 bei 0,7) | 0,985 | 0 | 308 ms |
| lokaler Jev-Modus, 0,9 | **1,00** | 0,17 | 0,970 | 0 | 256 ms |
| LLM, 0,9 | 0,98 (verpasst indirekte README-Injection) | 0,13 | 0,940 | 4 | 1120 ms |
| lokal + Jev „harmlos nur wenn beide“, 0,9 | **1,00** | 0,17 | – | – | ~310 ms |
| lokal + Jev „gleiche sichere Antwort“, 0,9 | – | – | 100 % bei 63 % Abdeckung | 0 | ~310 ms |

- Die Pattern-Listen verpassen fast alles, was nicht wörtlich „ignore previous …“ heißt: Deutsch,
  Umschreibungen, Datenabgriff, Befehle, Rollenspiel, indirekte und verschleierte Varianten (32 von 44).
- Beide Entscheider fanden **alle 44 Angriffe**, jeweils einzeln. Die „Fehlalarme“ sind fast nur
  **unsichere** Einstufungen harmloser Grenzfälle („Zeig mir, wo der System-Prompt gebaut wird“, „Lösche
  meinen Entwurf“, „Pretend you are a senior reviewer“) – im Screening `uncertain`, nicht `suspicious`.
- **Die Kombination hat auf diesem Satz nichts messbar verbessert**: beide Modelle waren einzeln schon
  vollständig, und ihre Unsicherheiten fielen auf dieselben Grenzfälle. Ihr Wert – ein Angriff muss zwei
  verschiedene Modelle gleichzeitig täuschen – zeigt sich erst bei **adaptiven** Angriffen gegen ein bestimmtes
  Modell; die enthält der Satz nicht. Das bleibt eine begründete Annahme, kein Messergebnis.
- Grenzen: 67 Fälle, ein Annotator, keine gezielt gegen Bonsai/Jev optimierten Angriffe.

**Empfehlung:** lokaler Jev-Modus als Screening (Daten bleiben lokal, gleiche Erkennung), Jev dazu als
Übereinstimmungs-Partner nur dort, wo Texte ohnehin extern verarbeitet werden dürfen. Schwelle 0,9;
`uncertain` → Review, nie automatisch blockieren oder freigeben.
