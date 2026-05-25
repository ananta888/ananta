# Onboarding Checklist to Learning Path Mapping

## Ziel

Die bestehende Onboarding-Checkliste als Startpunkt fuer einen strukturierten Lernpfad verwenden.

## Mapping

- Checklistenpunkte werden auf Einstiegskurse und sichere Basisuebungen abgebildet.
- Blueprint-Empfehlungen werden als Lernpfad-Empfehlungen wiederverwendet.
- First-Run-Hinweise werden mit Kursvoraussetzungen verknuepft.

## Fortschrittsabgrenzung

- Der bisherige lokale `localStorage`-Fortschritt bleibt UI-Hinweis.
- Er ist **nicht** das dauerhafte LearningProgress-Modell.

## Migrationskonzept

1. UI-only Fortschritt weiter anzeigen (kompatibel bleiben).
2. Parallel backendseitiges LearningProgress-Modell einfuehren.
3. Schrittweise auf serverseitig auditierbaren Lernfortschritt umstellen.
