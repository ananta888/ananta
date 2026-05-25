# Course Scaffold Template

## Ziel

Standardisierte Minimalstruktur fuer neue Kurse unter `docs/learning/courses`.

## Ordner- und ID-Konvention

- Ordnername: kebab-case, z. B. `ananta-secure-ai-basics`
- `course.id`: identisch zum Ordnernamen
- Lesson-/Exercise-/Assessment-IDs: `<course-id>-<topic>`

## Pflichtstruktur

```text
docs/learning/courses/<course-id>/
  course.json
  lessons/
    intro.md
  exercises/
    core-check.json
  assessments/
    final-check.json
```

## course.json Pflichtfelder

- `id`
- `title`
- `version`
- `status`
- `prerequisites`
- `grants_required`
- `security_boundaries`
- `learning_goals`
- `unlock_rules`

## Template-Snippet

```json
{
  "id": "ananta-example-course",
  "title": "Example Course",
  "version": "1.0",
  "status": "planned",
  "prerequisites": [],
  "grants_required": ["view"],
  "security_boundaries": ["no-production-data", "no-real-secrets"],
  "learning_goals": ["Explain secure behavior with deterministic checks"],
  "unlock_rules": [{"condition": "assessment_passed", "unlock": "next-course-id"}]
}
```

## Kompatibilitaet

Die Struktur ist kompatibel mit den bereits vorhandenen Dokumenten in `docs/learning` und trennt klar Kursmetadaten, Lerninhalt, Uebungen und Assessments.
