# Beta Feedback Interview Guide

## Interview Structure (30 minutes)
- Introduction & Context (5 min)
- Core Questions (20 min)
- Open Feedback & Next Steps (5 min)

## Introduction Script
"Thank you for participating in the Ananta beta program. This 30-minute interview helps us understand your experience and identify improvements. Your feedback is confidential and will guide our v1.0 roadmap. Feel free to be honest about what works and what doesn't."

---

## Week 1: Onboarding + First Task Completion

### Setup & Environment
1. Which environment did you choose (Docker/Local/Other) and why?
2. How long did it take from clone to first successful login?
3. What was the most confusing part of the setup process?
4. Did you encounter any blockers during installation?

### First Impressions
5. What was your first workflow/feature you tried?
6. Were you able to complete a task successfully? If not, where did you get stuck?
7. How intuitive was the navigation and UI layout?
8. Did you need to refer to documentation frequently? Which parts?

### Auth & Security
9. Did you experience any authentication issues (login, MFA, password)?
10. How clear were the API auth instructions?

---

## Week 2: Team/Role Setup and Templates Workflows

### Team & Role Management
1. Did you set up teams or roles? How was that experience?
2. Were permissions and access controls clear?
3. Did you encounter any issues with multi-user scenarios?

### Templates & Workflows
4. Did you use task templates? Were they helpful?
5. What workflows did you test this week?
6. Which features felt most valuable for your daily work?
7. What features are missing that you expected to find?

### Reliability
8. Did you experience any crashes, errors, or unexpected behavior?
9. How would you rate the system stability (1-5)?

---

## Week 3: Agent Panel + LLM-Assisted Flows

### Agent Panel
1. Did you explore the Agent Panel? What was your impression?
2. Were the agent capabilities and limitations clear?
3. Did you successfully trigger an agent workflow?
4. How transparent was the agent's decision-making process?

### LLM Integration
5. Did you use any LLM-assisted features (auto-planning, suggestions)?
6. How accurate and helpful were the AI-generated outputs?
7. Did you encounter any unexpected or incorrect agent behavior?

### Advanced Features
8. Which advanced features did you explore (webhooks, triggers, benchmarks)?
9. What documentation or examples would have helped you understand these better?

---

## Week 4: Stability and Performance Checks

### Performance
1. How responsive does the application feel overall?
2. Did you notice any slow operations or bottlenecks?
3. How was the performance with larger datasets or multiple concurrent tasks?

### Reliability & Error Handling
4. How often did you encounter errors or failures?
5. When errors occurred, were the messages clear and actionable?
6. Did you lose any work due to crashes or bugs?

### E2E Testing
7. Did you run the E2E tests? Were they reliable?
8. Did the tests help you understand the system better?

### Overall Assessment
9. What are the top 3 things you like most about Ananta?
10. What are the top 3 pain points or frustrations?
11. Would you recommend Ananta to a colleague? Why or why not?
12. What would make you choose Ananta over alternatives?

---

## Closing Questions (All Weeks)

### Security & Compliance
- Do you have any security concerns or compliance requirements we should address?
- Are there specific audit or logging features you need?

### Future Needs
- What features would you prioritize for v1.0?
- Are there integrations or plugins you'd like to see?

### Follow-up
- Can we contact you for follow-up questions?
- Would you be interested in testing future releases?

---

## Post-Interview Actions
1. Update `beta-feedback-tracking.csv` with key findings
2. Tag relevant issues with `beta-feedback` label
3. Add critical blockers to `todo.json` as high-priority tasks
4. Summarize weekly insights in `docs/roadmap.md` under Stakeholder Feedback
5. Schedule follow-up if needed
