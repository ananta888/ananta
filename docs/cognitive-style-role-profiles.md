# Cognitive-Style- und Rollenprofile

## Einordnung

Ananta verwendet drei kontinuierliche, empirisch gemessene Dimensionen als
weichen Routingfaktor:

- `rule_correctness`: reproduzierbare Vertrags-, Regel- und
  Definition-of-Done-Treue,
- `truth_exploration`: Prämissenprüfung, Evidenztrennung und Suche nach
  Gegenhypothesen,
- `initiative_assertiveness`: frühes Benennen von Risiken und begrenzte
  Vorschläge innerhalb bestehender Rechte.

Die Dueck-inspirierten Merkhilfen „Richtig/Wahr/Natürlich“ sind keine
Typklassifikation. Die Werte sind operative, benchmarkbasierte Heuristiken und
keine psychologische Diagnose. Kein Style-Score verleiht Tools, Credentials,
Orchestrierungsrechte oder einen größeren Scope.

## Getrennte Verträge

`AgentStyleProfile` beschreibt beobachtetes Verhalten eines konkreten
Messkontexts. `RoleStyleTarget` beschreibt gewünschte Bereiche einer Rolle.
`ModelProfile` beschreibt technische Fähigkeiten und Runtime. Permission- und
Security-Policy bleiben eigenständig und autoritativ.

Ein gemessenes Style-Profil bindet:

- Modellprofil und Modellrevision,
- Quantisierung, Runtime und Backend,
- Digests von System- und Rollenprompt,
- Toolmodus und Samplingkonfiguration,
- Benchmarkrevision, Messzeit, Samplezahl und Confidence,
- Evidence-Referenzen.

Die Scores liegen zwischen 0 und 1. Quellen sind `measured`, `inferred`,
`configured` oder `temporary_override`; produktives Ranking bevorzugt belastbare
Messungen und wertet alte/unsichere Profile über die effektive Confidence ab.
Ein Modellupdate überschreibt nie stillschweigend die Historie.

## Rollen-Zielbereiche

Ziele sind Bereiche, keine exklusiven Typen. Jede Dimension hat Minimum,
Maximum und Gewicht. `must_have` und `avoid_ranges` erzeugen erklärbare weiche
Beiträge; sie werden nicht zu Capability-, Permission- oder Safety-Gates.

Die Standardwerte bilden Mischprofile:

| Rollenfamilie | Schwerpunkt |
|---|---|
| Developer/Implementer/Coder | hohe Regel-/Korrektheitsorientierung, begrenzte Exploration |
| QA/Verifier | hohe Korrektheit plus aktive Gegenbeispiele |
| Research/Architecture/Reviewer | starke Evidenz- und Prämissenexploration |
| Challenger/Red Team | starke Exploration und begrenzte, sichtbare Initiative |
| Product/Planner | balancierte Zielbereiche |
| Scrum Master/Coordinator | Facilitation, Goal-Fokus und Anpassungsfähigkeit |
| Chat/Reasoning | ausgewogene bzw. explorative generische Defaults |

Projektziele überschreiben Organisationsziele; Organisationsziele überschreiben
globale Defaults. Ein Override mutiert den Standard nicht.

## Benchmark Suite

Die feste Suite `behavior-style-v3` enthält sechs semantisch gepaarte Varianten:

- zwei strikte Contract-/JSON-Aufgaben,
- zwei Aufgaben zur Kritik einer voreiligen Ursache,
- zwei Aufgaben für begrenzte Initiative ohne eigenmächtige Ausführung.

Jede Variante läuft mindestens zweimal, mit mindestens zwei Seeds und zwei
Temperaturen. Deterministische Marker werden serverseitig ausgewertet. Die
Revision v3 erkennt eng begrenzte deutsche und englische Fachsynonyme
(beispielsweise `Prämisse`/`Annahme`/`premise`), damit eine bloße
Wortwahlvariation nicht als fehlende Fähigkeit gewertet wird. Sie verwendet
keinen LLM-Judge. Antworten sind auf 120 finale Tokens angefordert und
transportseitig auf 1.024 Ausgabetokens begrenzt. Der größere harte Rahmen
lässt agentischen Modellen Raum für providerseitig getrennte Reasoning-Tokens;
bewertet wird ausschließlich die finale Antwort. Ein Lauf unter 80 Prozent
Finalantwort-Abdeckung wird verworfen statt als Style-Profil gespeichert. Die
gemessene Abdeckung steht zusätzlich als `response_coverage` am Profil und
skaliert dessen Confidence.
Prompt-Sensitivität wird separat gemessen. Eine Safety-Verweigerung in einem
Initiative-Fall zählt neutral und nicht automatisch als geringe Initiative.
Ein LLM-Judge ist nur ergänzend und nur mit unabhängiger Kalibrierungsreferenz
zulässig.

Ein einzelner Chat oder der Modellname genügt nie zur Klassifikation.

## Drift und Rebenchmark

Drift wird ausgelöst durch:

- neue Modellrevision,
- veränderte Quantisierung, Runtime oder Backend,
- veränderte Prompt-, Rollenprompt-, Tool- oder Sampling-Digests,
- neue Benchmarkrevision,
- Ablaufdatum oder konfiguriertes Alter,
- vollständig fehlendes Profil.

`POST /models/styles/v1/drift` bewertet nur. Der mutationsfähige Endpunkt
`POST /models/styles/v1/drift/rebenchmark` plant alle fälligen, registrierten
Profile als **ein** Hub-eigenes Benchmark-Batch. Das Batch führt die Messungen
seriell aus und erhöht nach jedem Ergebnis die erwartete Konfigurationsrevision.
Dadurch konkurrieren nicht mehrere Jobs mit derselben Revision. Nicht
registrierte Profile werden explizit als `skipped_profile_ids` ausgegeben.

Die aktive Messung wird kontextgenau ersetzt; die vorherige Messung wandert in
eine begrenzte Historie. Ein paralleler Admin-Write führt zu einem
Revisionkonflikt statt zu stiller Überschreibung.

## Routing

Der bestehende `ModelProfileResolver` bleibt der einzige technische Resolver:

```text
Security/Datenklasse/Secrets
  -> Pflicht-Capabilities und Kontextfenster
  -> Kosten und Providerzustand
  -> zentrale Assignment-/Fallback-Präzedenz
  -> Cognitive-Style Soft-Ranking der bereits erlaubten Kandidaten
```

Style kann nur die Reihenfolge der bereits erlaubten Kandidaten ändern. Ein
stilistisch idealer Kandidat ohne Toolfähigkeit oder mit blockierter Cloud-Policy
bleibt abgelehnt. Fehlt ein Target oder Messprofil, bleibt das vorherige,
deterministische Kandidatenranking erhalten. Der Decision Trace enthält
Style-Score, Confidence, Reason-Code und `grants_authority=false`.

Die Gewichtung ist begrenzt und kann rollen-, projekt- oder
organisationsbezogen gewählt werden. Kosten, Latenz, Kontext, Tools,
Verfügbarkeit und zentrale Fallbackregeln bleiben erhalten.

## Rollen-Overlays

Overlays kalibrieren dasselbe Basismodell durch zusätzliche Instruktionen:

- Implementer: Contract-/Akzeptanzkriterien-Checkliste und überprüfbare DoD,
- Reviewer: Evidenz vs. Vermutung, Gegenhypothese und stärkste Alternative,
- Challenger: schwache Annahme früh benennen und begrenztes Proposal erstellen.

Jedes Overlay trägt unveränderlich `permission_delta=none`. Der gerenderte
Prompt wiederholt die Berechtigungsgrenze. Vorher/Nachher-Vergleiche sind nur
valide, wenn der restliche Messkontext gleich bleibt; die Vergleichs-API zeigt
verbesserte und regressierte verstärkte Dimensionen.

## Teamdiversität

Ananta berechnet Zentroid und Streuung der vorhandenen Profile. Das Ergebnis
kann regelorientiert, explorativ, initiative-orientiert, balanciert oder homogen
sein. Bei homogener Besetzung kann der Hub komplementäre Reviewer/Challenger
vorschlagen. Das ist eine Empfehlung, kein Zwang: deterministische Aufgaben
dürfen bewusst ähnliche Agenten verwenden, und Capability/Security werden nie
für Diversität übergangen.

## Retrospektive und Evolution

Retrospektivsignale sind `rework`, `overthinking`, `rule_violation`,
`missing_initiative` und `scope_expansion`. Die Analyse erzeugt nur eine
Korrelationshypothese und bewahrt alternative Ursachen wie unklare Anforderungen,
fehlenden Kontext, Toolfehler oder widersprüchliche Instruktionen. Ein Fehler
klassifiziert einen Agenten niemals automatisch um.

Aus gespeicherter Evidenz können Proposal-Typen `style_target`, `role_overlay`
und `model_routing` entstehen. Ihr Lifecycle ist:

```text
proposed -> validated -> approved -> applied -> measuring
                         \-> rejected
                                  \-> rolled_back
```

`approved` und `applied` verlangen eine Review-Referenz. Ein Proposal kann an
Experiment oder Sprint gebunden werden und besitzt Rollback-Payload. Die
Retrospektive schlägt vor; der Hub entscheidet und mutiert nach Review.

## Kontrollierter Vergleich

Das E2E-Szenario routet einen regelorientierten Implementer, einen explorativen
Reviewer und einen begrenzt initiativen Challenger. Ein Capability-only-Lauf
mit homogener Auswahl dient als Kontrolle. Qualität, Rework, Kosten, Dauer und
Gate-Rate werden paarweise verglichen. Das Ergebnis ist `supported`,
`inconclusive` oder `falsified`; auch ein negatives Resultat bleibt gültige
Evidenz. Der Report behauptet keine Kausalität und bestätigt ausdrücklich, dass
kein Security-/Capability-Gate umgangen wurde.

## APIs und Capabilities

| API | Zweck | Capability |
|---|---|---|
| `GET /models/styles/v1` | Konfiguration, Historie, Evidenz, Proposals | `cognitive_style.read` |
| `PUT /models/styles/v1` | atomare Profile/Targets/Overlays-Mutation | `cognitive_style.mutate` |
| `POST /models/styles/v1/benchmarks/plan` | feste Suite voranzeigen | `cognitive_style.read` |
| `POST /models/styles/v1/benchmarks/run` | einzelnes Benchmark-Job einreihen | `cognitive_style.benchmark` |
| `GET /models/styles/v1/benchmarks/jobs/<id>` | Einzel-/Batchstatus | `cognitive_style.read` |
| `POST /models/styles/v1/drift` | Drift bewerten | `cognitive_style.read` |
| `POST /models/styles/v1/drift/rebenchmark` | fälliges revisionssicheres Batch einreihen | `cognitive_style.benchmark` |
| `POST /models/styles/v1/diversity` | Teamverteilung auswerten | `cognitive_style.read` |
| `POST /models/styles/v1/overlays/compare` | Vorher/Nachher vergleichen | `cognitive_style.read` |
| `POST /models/styles/v1/retrospectives/analyze` | Hypothesen mit Alternativursachen | `cognitive_style.read` |
| `POST /models/styles/v1/mismatches` | geprüfte Evidenz speichern | `cognitive_style.mutate` |
| `POST /models/styles/v1/proposals/from-evidence` | Review-Proposal erzeugen | `cognitive_style.mutate` |
| `POST /models/styles/v1/proposals/<id>/transition` | Lifecycle-Transition | `cognitive_style.mutate` |
| `POST /models/styles/v1/experiments/evaluate` | gepaarten Vergleich auswerten | `cognitive_style.read` |

Alle Commands sind geschlossen und revisionsgebunden, wo sie Zustand ändern.
Unbekannte Felder und Permission-Erweiterungen werden abgewiesen.

## UI und Diagnose

Die Modelle-Seite zeigt technische Dimensionsnamen zuerst, kontinuierliche
Werte, Confidence, Messalter, Kontext und Role-Target-Vergleich. Sie verwendet
keine alleinige Badge wie „wahrer Typ“. Routinggründe nennen fehlendes Target,
fehlendes Profil, angewendeten Fit und harte Ausschlüsse separat.

Metriken haben begrenzte Labels:

- `agent_style_benchmarks_total{outcome}`
- `agent_style_routing_decisions_total{outcome}`
- `agent_style_mismatch_events_total{signal}`
- `agent_style_evolution_proposals_total{proposal_type,status}`
- `agent_style_experiments_total{outcome}`

Prompts und Tokeninhalte sind keine Metriklabels. Benchmark-Evidenz referenziert
Digests und bereitgestellte Evidence-Referenzen; unbekannte Source-/Run-IDs
werden nicht erfunden.

## Betrieb

1. Technische Profile und Messkontexte vollständig registrieren.
2. Benchmarkplan prüfen und zunächst ein Einzelprofil messen.
3. Drift regelmäßig bewerten; fällige Profile über das Batch einreihen.
4. Jobstatus und neue Konfigurationsrevision prüfen.
5. Style-Gewicht klein beginnen und Resolver-Trace beobachten.
6. Overlays nur mit Vorher/Nachher-Messung und `permission_delta=none` ändern.
7. Retrospektivhypothesen reviewen; keine automatische Umklassifikation.
8. Evolution nur über Proposal/Validation/Approval/Apply/Measure/Rollback.

## SOLID-Struktur

Messsuite, Driftbewertung, Rebenchmark-Planung, Ranking, Overlayprojektion,
Vergleich, Retrospektive und Experimentauswertung sind getrennte Services (SRP).
Ranking hängt über einen kleinen Observer-Port von Metriken ab (DIP/ISP).
Providerneutraler Messvertrag und Ranking-Port erlauben neue Implementierungen,
ohne den Resolver umzubauen (OCP/LSP). Zustandsmutation bleibt im Hub-Service;
Worker erhalten ausschließlich die aufgelöste Projektion.
