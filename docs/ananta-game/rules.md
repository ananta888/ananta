# Ananta Strategy Game Regeln

## Ziel

Das Spiel abstrahiert reale Entwicklungsarbeit als regelbasierten Flow:
`Goal -> Plan -> Task -> Action -> Verification -> Artifact`.

Ein Zug gilt nur dann als erfolgreich, wenn er policy-konform ist und verifizierte Evidence erzeugt.

## Regelmodule

## CodeAegis

- Schuetzt Code-Territorien gegen riskante Aktionen.
- Blockiert unautorisierte oder unzureichend verifizierte Schreibzugriffe.
- Erhoeht Risiko, wenn Actions ohne Tests/Review ausgefuehrt werden.

## AegisFlow

- Erzwingt Reihenfolge und Abhaengigkeiten von Arbeitsschritten.
- Verhindert Abkuerzungen, die Verification oder Artifact-Erzeugung umgehen.
- Modelliert Retry und Rollback als explizite Folgezuege.

## AegisHub

- Ist der einzige Orchestrator fuer Delegation und Freigaben.
- Verwaltet Goal-, Task- und Approval-Zustaende.
- Nimmt Evidence entgegen und entscheidet ueber Abschluss oder Rework.

## AgentAegis

- Definiert Rollen, Faehigkeiten und Grenzen von Agenten.
- Erzwingt Default-Deny fuer unbekannte Aktionen.
- Verhindert Worker-zu-Worker-Orchestrierung.

## DevAegis

- Modelliert CI, Review und Branch-Schutz als Gate-Mechaniken.
- Penalisiert Deploy/Integrationsaktionen ohne gruenen Testzustand.

## ContextAegis

- Modelliert Sichtbarkeit als Fog-of-War.
- Begrenzt Kontextzugriff nach Rolle, Policy und Klassifikation.
- Trennt lokale und Cloud-Ausfuehrung regelbasiert.

## ArtifactGuard

- Akzeptiert Abschluss nur mit verifizierbarer Evidence.
- Unterscheidet behaupteten von verifiziertem Fortschritt.
- Markiert fehlende/alte Artefakte als Failure oder Stale.

## TrustWeave

- Fuehrt einen Vertrauensgraphen zwischen Agenten, Territorien und Policies.
- Passt Trust-Werte auf Basis erfolgreicher/fehlgeschlagener Zuege an.

## CodeCompass

- Liefert die erklaerbare Kartengrundlage (Territorien, Abhaengigkeiten, Risiken).
- Fehlende Daten fuehren zu einem degradierten, aber gueltigen Zustand.

## NagaCore

- Repräsentiert Systemstabilitaet und Leitenergie.
- Kann als Tutorial-/Guide-Mechanik fuer Regeln und Zustandsuebergaenge dienen.

## Invarianten

1. Hub bleibt zentraler Kontrollpunkt; keine Worker-zu-Worker-Orchestrierung.
2. Kontext ist standardmaessig deny/hidden, bis explizit freigegeben.
3. Fortschritt ohne Artifact/Evidence ist nicht verifiziert.
4. Riskante Mutationen brauchen Approval + Audit-Pfad.
