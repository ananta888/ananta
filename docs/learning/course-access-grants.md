# Course Access Grants

## Modell: `CourseAccessGrant`

Empfohlene Felder:

- `id`
- `course_id`
- `lesson_id` (optional)
- `user_id` oder `team_id`
- `grant_type`
- `expires_at` (optional)
- `reason`
- `status` (`active|expired|revoked`)
- `created_by`, `created_at`

## Grant-Typen

- `view`
- `execute_exercise`
- `use_worker`
- `decrypt_training_artifact`
- `remote_llm_allowed`
- `mentor_review`

## Regeln

- Grants koennen zeitlich begrenzt sein.
- Grants entstehen durch deterministische Checks oder explizites Approval.
- `remote_llm_allowed` wird nie implizit aus `use_worker` abgeleitet.
- Revocation/Expiry blockieren neue sicherheitsrelevante Aktionen.
