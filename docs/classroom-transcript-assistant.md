# Classroom transcript assistant

The classroom subsystem accepts signed webhook events, authenticated MCP
events, and deterministic batch fixtures. The hub owns admission, policy,
deduplication, routing, and the resulting TeacherActionCard. Analysis
components are exposed behind injectable service boundaries so execution can
be delegated without worker-to-worker orchestration.

## Configuration

The feature is opt-in:

```yaml
classroom:
  enabled: true
  webhook_secrets:
    zoom: "${CLASSROOM_ZOOM_WEBHOOK_SECRET}"
  question_confidence_threshold: 0.6
  retention_hours_raw_segments: 72
  max_context_tokens: 2000
  room_mappings:
    room-a:
      group: cohort-a
      module_scope: M04
  schedule:
    - day: mon
      start: "09:00"
      end: "10:30"
      module_id: M04
      task_id: M04-A1
```

Webhook signatures use `X-Classroom-Signature: sha256=<hex-hmac>`. Unknown
sources and invalid signatures are rejected before event processing. Speaker
labels are converted to bounded `spk-<hash>` aliases and transcript text is
redacted before it enters session memory, cards, prompts, or audit metadata.
Raw session segments expire after `retention_hours_raw_segments`; cards have a
separate lifecycle.

## Teaching index

Run CodeCompass with
`rag-helper/profiles/teaching-materials.json`. Markdown frontmatter supports
`module_id`, `task_id`, `material_kind`, `day`, `schedule_slot`, and
`related_n8n_workflows`. Room and schedule data are context hints only;
answers require material evidence.

## Read-only n8n boundary

The assistant proposes and exports reviewed fragments only. It never calls an
n8n deployment/import API and never modifies a learner workflow. Failed
verification blocks export; warning results carry a visible warning banner.
Active import/deployment requires a separate future track with explicit
authorization, review, audit, and rollback design.

## Operations

The Angular route `/classroom` lists cards and separates material evidence
from room/schedule hints. Operators can copy an answer, export a verified
workflow fragment, or mark a card `answered`/`dismissed`. Audit actions are
`classroom_event_received`, `classroom_answer_proposed`,
`classroom_workflow_proposed`, `classroom_card_created`, and
`classroom_workflow_exported`.
