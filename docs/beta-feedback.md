# Beta Feedback Plan

## Goals
- Validate onboarding clarity and setup time.
- Measure task completion success for common workflows.
- Identify top UX friction points in the dashboard.
- Collect reliability signals for E2E and agent orchestration.

## Stakeholder Inputs (Summary)
- Need faster setup and fewer manual steps for local runs.
- Want clearer API auth guidance and examples.
- Prefer structured feedback loops before v1.0.

## Audience
- Internal power users (engineering/QA).
- Pilot customers (2-5 teams).
- Community testers (opt-in).

## Feedback Channels
- Short survey (10-15 questions) - see Survey Template below.
- Weekly 30-min interviews with 3-5 users.
  - **Interview Guide**: `docs/beta-interview-guide.md`
  - Structured questions for each week of the timeline
- Issue tracker labels: `beta-feedback`, `onboarding`, `reliability`.
  - Create these labels in your issue tracker (GitHub/GitLab/etc.)
  - Tag all beta-related issues for easy filtering and analysis

## Timeline
- Week 1: Onboarding + first task completion.
- Week 2: Team/role setup and templates workflows.
- Week 3: Agent panel + LLM-assisted flows.
- Week 4: Stability and performance checks.

## Tracking
- Central sheet: user, environment, task success, blockers, notes.
  - **File**: `docs/beta-feedback-tracking.csv`
  - Update after each survey response or interview
- Weekly summary in `docs/roadmap.md` under Stakeholder Feedback.

## Survey Template (Draft)
1. Which environment did you use? (Docker/Local/Other)
2. Time to first successful login (minutes)?
3. Which workflow did you try first? (Tasks/Teams/Templates/Agent Panel)
4. Where did you get stuck, if anywhere?
5. Rate the clarity of setup instructions (1-5).
6. Rate the reliability of E2E tests (1-5).
7. Did you encounter auth issues (login/MFA/password)? (Yes/No)
8. How clear are error messages? (1-5)
9. What feature is missing for your daily work?
10. Any security concerns or compliance requirements?
