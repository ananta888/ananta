# Course UI Concept

## Kernansicht

Die Kurs-UI zeigt:

- Lernpfade und Kurse
- Lektionen mit Voraussetzungen
- Progress-Status je Nutzer
- naechste sichere Uebung

## Rechte- und Sicherheitsanzeige

Pro Uebung sichtbar:

- benoetigte Rechte (`view`, `execute_exercise`, `use_worker`, `remote_llm_allowed`)
- Sandbox-Grenzen (Tools, Datenquellen, Runtime)
- Risiko-Level und Freigabestatus

## Unlock-Transparenz

- UI zeigt, welche Rechte/Kurse durch Abschluss freigeschaltet werden koennen.
- Gesperrte oder riskante Features sind sichtbar markiert (nicht versteckt).
- Freischaltungen ohne bestandenen Check bleiben blockiert (Default-Deny).

## Review-Punkte

- Sensitive Pfade markieren Human-Approval-Bedarf.
- Audit-Hinweis pro Freigabeentscheidung anzeigen.
