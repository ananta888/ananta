# Learning Progress Model

## Datenmodell

`LearningProgress`:

- `user_id`
- `course_id`
- `lesson_id`
- `status` (`not_started|in_progress|passed|failed|review_required`)
- `score`
- `attempts`
- `completed_at`
- `last_event_id`

## Abgrenzung zu Onboarding-UI-Progress

- Der bisherige localStorage-Progress aus der Onboarding-UI bleibt ein lokaler UX-Hinweis.
- `LearningProgress` ist das serverseitige, auditierbare System-of-Record fuer Kursfortschritt.
- Migration erfolgt kompatibel: UI kann lokal anzeigen, Rechteentscheidungen basieren aber auf backendseitigem LearningProgress.

## Freischaltungslogik

- Freischaltungen entstehen nur aus definierten Regeln.
- `passed` kann Unlocks triggern, aber nur fuer explizit konfigurierte Ziele.
- Kein automatischer Rights-Grant ohne Mapping auf `CourseAccessGrant`.

## Audit

- Jeder Statuswechsel wird als Event mit Zeitstempel und Quelle protokolliert.
- Mentor/Admin-Override erzeugt eigenes Event inklusive Begruendung.
- Progress-Events sind mit Course/Assessment-Knoten korrelierbar.
