# Course File Structure

## Zielstruktur

```text
docs/learning/
  least-privilege-course-system.md
  course-file-structure.md
  course-access-grants.md
  learning-progress-model.md
  assessment-model.md
  course-badges.md
  courses/
    <course-slug>/
      course.json
      lessons/
        <lesson-slug>.md
      exercises/
        <exercise-slug>.json
      assessments/
        <assessment-slug>.json
```

## Konventionen

- `course.json`: Metadaten, prerequisites, unlocks, grants_required, security_boundaries.
- `lesson.md`: Lerninhalt, Beispiele, Risiken, Review-Hinweise.
- `exercise.json`: Aufgabe, Eingaben, erwartete Checks, Sicherheitsgrenzen.
- `assessment.json`: Bewertungslogik, deterministische Checks, Unlock-Regeln.

## Pflichtfelder

Jede Lektion/Uebung muss enthalten:

- Voraussetzungen (`prerequisites`)
- moegliche Freischaltungen (`unlocks`)
- Sicherheitsgrenzen (`security_boundaries`)
- klare Acceptance Criteria

## Sicherheitsregel

Default-Deny gilt fuer Kursfreischaltungen: ohne bestandenen Check oder freigegebenes Review keine Rechte-Eskalation.
