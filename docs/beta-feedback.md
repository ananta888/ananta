# Beta Feedback Plan

## Ziele
- Reale Nutzungsszenarien validieren
- Onboarding-Hürden identifizieren
- Sicherheits- und Permission-Model prüfen
- Performance und Stabilität unter Last testen

## Phasen & Zeitplan

| Phase | Fokus | Dauer | Zielgruppe |
|-------|-------|-------|------------|
| Alpha | Core-Entwickler & Intern | 2 Wochen | Team Ananta |
| Beta 1 | Early Adopters (Technisch) | 4 Wochen | Ausgewählte Devs |
| Beta 2 | Erweiterte Nutzergruppe | 4 Wochen | Community |

## Feedback-Kanäle
- **Kurz-Survey**: Nach der ersten Woche und nach Abschluss der Beta.
- **Interviews**: 30-minütige Calls mit Power-Usern.
- **GitHub Issues**: Technisches Feedback und Bug-Reports.
- **Audit-Logs**: Analyse (anonymisiert) der häufigsten Fehlermeldungen.

---

## Fragebogen-Template (Survey)

### 1. Erster Eindruck
- Wie einfach war die Installation? (1-5)
- Waren die Quickstart-Anweisungen klar verständlich?
- Was war die größte Hürde beim Setup?

### 2. Funktionalität
- Welches Feature nutzen Sie am häufigsten? (Tasks, Templates, Teams, Dashboard)
- Gab es Momente, in denen das System nicht wie erwartet reagiert hat?
- Welche Agent-Rollen vermissen Sie aktuell?

### 3. Benutzererfahrung (UX)
- Wie bewerten Sie die Übersichtlichkeit des Dashboards? (1-5)
- Ist die Echtzeit-Einsicht in die Logs hilfreich?
- Vermissen Sie spezifische Visualisierungen?

### 4. Sicherheit & Vertrauen
- Fühlen Sie sich sicher bei der Ausführung von Shell-Befehlen durch Agenten?
- Ist das Rollenmodell (Admin/User) für Ihren Anwendungsfall ausreichend?
- Wie bewerten Sie die MFA-Implementierung?

### 5. Abschließendes Feedback
- Würden Sie Ananta Kollegen empfehlen?
- Was ist das eine Feature, das wir unbedingt als Nächstes bauen sollten?

---

## Metriken
- **Time-to-first-task**: Zeit von Installation bis zum ersten erfolgreichen Task-Run.
- **Erfolgsrate**: Verhältnis von `propose` zu `execute` (Akzeptanz von LLM-Vorschlägen).
- **Fehlerquote**: Häufigkeit von 5xx Fehlern in den API-Logs.
- **Retention**: Wie viele Nutzer kehren nach der ersten Woche zurück?

## Ergebnis-Tracking
- Feedback wird wöchentlich konsolidiert.
- Kritische Bugs fließen sofort in den Sprint ein.
- Feature-Wünsche werden in `docs/roadmap.md` priorisiert.
- Erledigte Maßnahmen werden in den Arbeitsberichten dokumentiert.
