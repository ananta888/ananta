# Course Preview from Demo Mode

## Ziel

Den bestehenden `DemoModeService` als read-only Grundlage fuer eine Kursvorschau nutzen.

## Mapping-Konzept

- `DemoExample` -> `CoursePreview`
- Demo-Tasks -> Lesson-/Exercise-Vorschau
- Demo-Artefakte -> Preview-Material mit klarer Read-only-Markierung

## Sicherheitsgarantien

- Read-only und isoliert bleiben verpflichtend.
- CoursePreview erzeugt keine produktiven Goals/Tasks.
- CoursePreview darf keinen produktiven State veraendern.

## Teststrategie

Die bestehenden Muster aus `tests/test_demo_mode.py` werden fuer CoursePreview-Tests wiederverwendet:

- read-only Verhalten
- Isolationsverhalten
- keine Seiteneffekte auf produktiven Task-Zustand
