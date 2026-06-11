import * as vscode from "vscode";
import { ApprovalRef, ArtifactRef, AuditRef, RepairRef } from "../views/sidebarProviders";
import {
  COMMANDS,
  RuntimeClientContext,
  capabilityRef,
  asRecord,
  readString,
  readItems,
  openJsonPanel,
  openWebFallbackInternal,
  redactSensitiveValue
} from "./helpers";
import { buildWebFallbackUrl } from "../runtime/webFallback";

export function isTextArtifact(payload: Record<string, unknown>): boolean {
  const type = readString(payload, "type", "artifact_type", "mime_type", "kind").toLowerCase();
  if (!type) {
    return true;
  }
  if (type.includes("text") || type.includes("report") || type.includes("diff") || type.includes("review")) {
    return true;
  }
  if (type.includes("json") || type.includes("markdown") || type.includes("log")) {
    return true;
  }
  if (
    type.includes("binary") ||
    type.includes("image") ||
    type.includes("pdf") ||
    type.includes("zip") ||
    type.includes("audio") ||
    type.includes("video")
  ) {
    return false;
  }
  return true;
}

export async function openArtifactDetailInternal(runtime: RuntimeClientContext, ref: ArtifactRef): Promise<void> {
  const artifact = await runtime.client.getArtifact(ref.id);
  if (!artifact.ok) {
    await openWebFallbackInternal(runtime, { target: "artifacts", id: ref.id, source: "artifact_detail_degraded" });
    void vscode.window.showWarningMessage(`Artifact detail degraded (${artifact.state}); browser fallback opened.`);
    return;
  }
  const payload = asRecord(artifact.data);
  if (!payload) {
    openJsonPanel(`Ananta Artifact ${ref.id}`, {
      schema: "vscode_artifact_detail_v1",
      artifact_id: ref.id,
      unsupported_payload: artifact.data,
      fallback_url: buildWebFallbackUrl(runtime.settings.baseUrl, "artifacts", ref.id)
    });
    return;
  }
  if (!isTextArtifact(payload)) {
    await openWebFallbackInternal(runtime, { target: "artifacts", id: ref.id, source: "artifact_binary" });
    void vscode.window.showInformationMessage("Binary/rich artifact opened in browser fallback.");
    return;
  }
  openJsonPanel(`Ananta Artifact ${ref.id}`, {
    schema: "vscode_artifact_detail_v1",
    artifact_id: ref.id,
    read_only_render: true,
    detail: payload
  });
}

export async function openApprovalDetailInternal(runtime: RuntimeClientContext, ref: ApprovalRef): Promise<void> {
  const approvals = await runtime.client.listApprovals();
  const all = readItems(approvals.data);
  const found = all.find((entry) => readString(entry, "id", "approval_id") === ref.id) ?? null;
  openJsonPanel(`Ananta Approval ${ref.id}`, {
    schema: "vscode_approval_detail_v1",
    approval_id: ref.id,
    queue_state: approvals.state,
    queue_status_code: approvals.statusCode,
    stale_or_missing_state_explicit: found === null,
    detail: found
  });
}

export async function openAuditDetailInternal(runtime: RuntimeClientContext, ref: AuditRef): Promise<void> {
  const auditLogs = await runtime.client.getAuditLogs(50, 0);
  const all = readItems(auditLogs.data);
  const found = all.find((entry) => readString(entry, "id", "audit_id", "event_id") === ref.id) ?? null;
  const relatedLinks = {
    goal: ref.relatedGoalId ? buildWebFallbackUrl(runtime.settings.baseUrl, "goals", ref.relatedGoalId) : null,
    task: ref.relatedTaskId ? buildWebFallbackUrl(runtime.settings.baseUrl, "tasks", ref.relatedTaskId) : null,
    artifact: ref.relatedArtifactId ? buildWebFallbackUrl(runtime.settings.baseUrl, "artifacts", ref.relatedArtifactId) : null,
    audit: buildWebFallbackUrl(runtime.settings.baseUrl, "audit", ref.id, ref.traceId)
  };
  openJsonPanel(`Ananta Audit ${ref.id}`, {
    schema: "vscode_audit_detail_v1",
    audit_id: ref.id,
    queue_state: auditLogs.state,
    queue_status_code: auditLogs.statusCode,
    stale_or_missing_state_explicit: found === null,
    related_refs: {
      goal_id: ref.relatedGoalId || null,
      task_id: ref.relatedTaskId || null,
      artifact_id: ref.relatedArtifactId || null,
      trace_id: ref.traceId || null
    },
    browser_fallback: relatedLinks,
    deep_analysis_note: "Use audit browser fallback for deep trace navigation.",
    detail: redactSensitiveValue(found)
  });
}

export async function openRepairDetailInternal(runtime: RuntimeClientContext, ref: RepairRef): Promise<void> {
  const [repairs, session] = await Promise.all([runtime.client.listRepairs(), runtime.client.getRepairSession(ref.id)]);
  const all = readItems(repairs.data);
  const summary = all.find((entry) => readString(entry, "session_id", "id", "repair_id") === ref.id) ?? null;
  const detail = session.ok ? session.data : summary;
  const parsed = asRecord(detail);
  const proposedSteps = parsed?.proposed_steps;
  openJsonPanel(`Ananta Repair ${ref.id}`, {
    schema: "vscode_repair_detail_v1",
    repair_session_id: ref.id,
    queue_state: repairs.state,
    queue_status_code: repairs.statusCode,
    detail_state: session.state,
    detail_status_code: session.statusCode,
    diagnosis: parsed ? readString(parsed, "diagnosis", "summary", "issue") || null : null,
    proposed_steps: Array.isArray(proposedSteps) ? proposedSteps : proposedSteps ? [proposedSteps] : [],
    dry_run_status: parsed ? readString(parsed, "dry_run_status", "dry_run_state") || null : null,
    approval_state: parsed ? readString(parsed, "approval_state", "approval_status") || null : null,
    verification_result: parsed ? readString(parsed, "verification_result", "verification_state") || null : null,
    execution_guardrail:
      "Opening/refreshing this view never executes repairs. Execution/approval remains backend-gated and explicit.",
    browser_fallback: buildWebFallbackUrl(runtime.settings.baseUrl, "repair", ref.id),
    detail: redactSensitiveValue(detail)
  });
}

export async function runApprovalAction(
  ref: ApprovalRef,
  action: "approve" | "reject",
  refreshSidebarData: (source: string) => Promise<RuntimeClientContext | null>
): Promise<void> {
  const runtime = await refreshSidebarData(`approval_${action}_preflight`);
  if (!runtime) {
    return;
  }
  const allowed = action === "approve" ? capabilityRef.current.approvalActions.approve : capabilityRef.current.approvalActions.reject;
  if (!allowed) {
    void vscode.window.showWarningMessage(`Approval action denied: ${action} is not permitted by backend capabilities.`);
    return;
  }

  const choice = await vscode.window.showWarningMessage(
    `${action === "approve" ? "Approve" : "Reject"} approval ${ref.id}?`,
    { modal: true, detail: "This calls backend approval APIs only; no local state mutation is performed." },
    action === "approve" ? "Approve" : "Reject",
    "Cancel"
  );
  if (!choice || choice === "Cancel") {
    return;
  }

  const comment = await vscode.window.showInputBox({
    title: `${action === "approve" ? "Approve" : "Reject"} approval ${ref.id}`,
    prompt: "Optional comment",
    ignoreFocusOut: true
  });
  const response =
    action === "approve"
      ? await runtime.client.approveApproval(ref.id, comment ?? "")
      : await runtime.client.rejectApproval(ref.id, comment ?? "");
  if (!response.ok) {
    void vscode.window.showWarningMessage(
      `Approval action degraded (${response.state}). Stale/denied/already-handled state may apply.`
    );
  } else {
    void vscode.window.showInformationMessage(`Approval ${ref.id} ${action === "approve" ? "approved" : "rejected"}.`);
  }
  await refreshSidebarData(`approval_${action}_complete`);
}
