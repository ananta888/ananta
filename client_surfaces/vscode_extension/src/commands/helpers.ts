import * as vscode from "vscode";
import { AnantaBackendClient } from "../runtime/backendClient";
import { toCommandContextKey, WorkflowCommandId, WORKFLOW_COMMANDS } from "../runtime/capabilityGate";
import { redactSensitiveText } from "../runtime/redaction";
import { AnantaSecretStore } from "../runtime/secretStore";
import { RuntimeSettings } from "../runtime/types";
import { buildWebFallbackUrl, fallbackTargetLabel, WebFallbackTarget } from "../runtime/webFallback";

export const COMMANDS = {
  checkHealth: "ananta.checkHealth",
  configureProfile: "ananta.configureProfile",
  storeToken: "ananta.storeToken",
  clearToken: "ananta.clearToken",
  openStatusView: "ananta.openStatusView",
  submitGoal: "ananta.submitGoal",
  analyzeSelection: "ananta.analyzeSelection",
  reviewFile: "ananta.reviewFile",
  patchPlan: "ananta.patchPlan",
  projectNew: "ananta.projectNew",
  projectEvolve: "ananta.projectEvolve",
  refreshSidebarData: "ananta.refreshSidebarData",
  setGoalTaskStatusFilter: "ananta.setGoalTaskStatusFilter",
  openGoalOrTaskDetail: "ananta.openGoalOrTaskDetail",
  openArtifactDetail: "ananta.openArtifactDetail",
  openApprovalDetail: "ananta.openApprovalDetail",
  openAuditDetail: "ananta.openAuditDetail",
  openRepairDetail: "ananta.openRepairDetail",
  openWebFallback: "ananta.openWebFallback",
  launchTui: "ananta.launchTui",
  approveApproval: "ananta.approveApproval",
  rejectApproval: "ananta.rejectApproval"
} as const;

export const APPROVAL_CONTEXT_KEYS = {
  approve: "ananta.capability.approvalApprove",
  reject: "ananta.capability.approvalReject"
} as const;

export interface RuntimeClientContext {
  client: AnantaBackendClient;
  settings: RuntimeSettings;
}

export interface CapabilityExecutionState {
  workflowAvailability: Record<WorkflowCommandId, boolean>;
  approvalActions: {
    approve: boolean;
    reject: boolean;
  };
}

export interface WebFallbackArgs {
  target?: WebFallbackTarget;
  id?: string;
  traceId?: string;
  source?: string;
}

export const capabilityRef: { current: CapabilityExecutionState } = {
  current: {
    workflowAvailability: workflowDefaultState(),
    approvalActions: { approve: false, reject: false }
  }
};

export function workflowDefaultState(): Record<WorkflowCommandId, boolean> {
  return WORKFLOW_COMMANDS.reduce(
    (acc, commandId) => {
      acc[commandId] = false;
      return acc;
    },
    {} as Record<WorkflowCommandId, boolean>
  );
}

export function defaultCapabilityState(): CapabilityExecutionState {
  return {
    workflowAvailability: workflowDefaultState(),
    approvalActions: { approve: false, reject: false }
  };
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

export function readString(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return "";
}

export function readItems(payload: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(payload)) {
    return payload.map((entry) => asRecord(entry)).filter((entry): entry is Record<string, unknown> => entry !== null);
  }
  const record = asRecord(payload);
  if (!record || !Array.isArray(record.items)) {
    return [];
  }
  return record.items.map((entry) => asRecord(entry)).filter((entry): entry is Record<string, unknown> => entry !== null);
}

export function redactSensitiveValue(value: unknown): unknown {
  if (typeof value === "string") {
    return redactSensitiveText(value);
  }
  if (Array.isArray(value)) {
    return value.map((entry) => redactSensitiveValue(entry));
  }
  const record = asRecord(value);
  if (!record) {
    return value;
  }
  const output: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(record)) {
    const lowered = key.toLowerCase();
    if (
      lowered.includes("token") ||
      lowered.includes("secret") ||
      lowered.includes("password") ||
      lowered.includes("authorization")
    ) {
      output[key] = "[REDACTED]";
      continue;
    }
    output[key] = redactSensitiveValue(entry);
  }
  return output;
}

export function firstRecord(payload: unknown): Record<string, unknown> | null {
  const items = readItems(payload);
  if (items.length > 0) {
    return items[0];
  }
  return asRecord(payload);
}

export function extractProviderSummary(providerPayload: unknown, catalogPayload: unknown): string {
  const providers = readItems(providerPayload);
  const names = new Set<string>();
  for (const provider of providers) {
    const name = readString(provider, "provider", "name", "id");
    if (name) {
      names.add(name);
    }
  }
  const catalog = asRecord(catalogPayload);
  if (catalog && Array.isArray(catalog.providers)) {
    for (const provider of catalog.providers) {
      if (typeof provider === "string" && provider.trim().length > 0) {
        names.add(provider.trim());
      }
    }
  }
  if (names.size === 0) {
    return "provider_summary=unavailable";
  }
  return `provider_summary=${Array.from(names).sort().join(",")}`;
}

export function extractModelSummary(benchmarksPayload: unknown): string {
  const top = firstRecord(benchmarksPayload);
  if (!top) {
    return "model_summary=unavailable";
  }
  const model = readString(top, "model", "model_name", "id", "provider_model");
  const score = readString(top, "score", "quality_score", "latency_score");
  if (!model) {
    return "model_summary=unavailable";
  }
  return score ? `model_summary=${model} (score=${score})` : `model_summary=${model}`;
}

export function extractGovernanceSummary(
  assistantPayload: unknown,
  configPayload: unknown,
  capabilityState: string,
  healthState: string
): string {
  const assistant = asRecord(assistantPayload);
  const config = asRecord(configPayload);
  const mode = assistant ? readString(assistant, "active_mode", "mode") : "";
  const governance = config ? readString(config, "governance_mode", "policy_mode", "policy_profile") : "";
  const policyState = capabilityState === "healthy" && healthState === "healthy" ? "ok" : "degraded";
  const parts = [mode ? `assistant_mode=${mode}` : "", governance ? `governance=${governance}` : "", `policy_state=${policyState}`]
    .filter(Boolean)
    .join(" ");
  return parts.length > 0 ? parts : "governance=unavailable";
}

export function extractTaskId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "task_id");
}

export function extractGoalId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "goal_id");
}

export function extractArtifactId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "artifact_id");
}

export function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function shellQuote(value: string): string {
  return "'" + String(value || "").replace(/'/g, "'\\''") + "'";
}

export function openJsonPanel(title: string, payload: unknown): void {
  const panel = vscode.window.createWebviewPanel("ananta.detailPanel", title, vscode.ViewColumn.Beside, {
    enableScripts: false
  });
  const json = JSON.stringify(payload, null, 2);
  panel.webview.html = [
    "<html><body>",
    '<style>body{font-family:var(--vscode-editor-font-family);padding:12px;} pre{white-space:pre-wrap;word-break:break-word;}</style>',
    `<pre>${escapeHtml(json)}</pre>`,
    "</body></html>"
  ].join("");
}

export function statusBarText(connectionState: string, capabilitiesState: string): string {
  if (connectionState === "healthy" && capabilitiesState === "healthy") {
    return "$(check) Ananta Connected";
  }
  if (connectionState === "invalid_config") {
    return "$(error) Ananta Invalid Config";
  }
  if (capabilitiesState === "capability_missing" || capabilitiesState === "policy_denied") {
    return "$(warning) Ananta Limited";
  }
  if (connectionState === "backend_unreachable" || connectionState === "backend_timeout") {
    return "$(error) Ananta Unreachable";
  }
  if (connectionState === "auth_failed") {
    return "$(error) Ananta Auth Failed";
  }
  return "$(warning) Ananta Degraded";
}

export function diagnosticSeverity(state: string): vscode.DiagnosticSeverity {
  if (state === "capability_missing" || state === "policy_denied") {
    return vscode.DiagnosticSeverity.Warning;
  }
  return vscode.DiagnosticSeverity.Error;
}

export async function applyWorkflowContexts(values: Record<WorkflowCommandId, boolean>): Promise<void> {
  await Promise.all(
    WORKFLOW_COMMANDS.map((commandId) => vscode.commands.executeCommand("setContext", toCommandContextKey(commandId), values[commandId]))
  );
}

export async function applyApprovalActionContexts(approve: boolean, reject: boolean): Promise<void> {
  await Promise.all([
    vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.approve, approve),
    vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.reject, reject)
  ]);
}

export async function storeTokenCmd(context: vscode.ExtensionContext, output: vscode.OutputChannel): Promise<void> {
  const value = await vscode.window.showInputBox({
    title: "Store Ananta Auth Token",
    prompt: "Enter token for current Ananta profile",
    password: true,
    ignoreFocusOut: true
  });
  if (!value) return;
  const config = vscode.workspace.getConfiguration("ananta");
  const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
  const secretStore = new AnantaSecretStore(context.secrets);
  await secretStore.storeToken(value, key);
  output.appendLine(`[auth] token stored with key=${key}`);
  void vscode.window.showInformationMessage("Ananta token stored in SecretStorage.");
}

export async function clearTokenCmd(context: vscode.ExtensionContext, output: vscode.OutputChannel): Promise<void> {
  const config = vscode.workspace.getConfiguration("ananta");
  const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
  const secretStore = new AnantaSecretStore(context.secrets);
  await secretStore.clearToken(key);
  output.appendLine(`[auth] token cleared with key=${key}`);
  void vscode.window.showInformationMessage("Ananta token removed from SecretStorage.");
}

export async function launchTuiCmd(runtime: RuntimeClientContext): Promise<void> {
  const terminal = vscode.window.createTerminal({
    name: "Ananta TUI",
    env: {
      ANANTA_BASE_URL: runtime.settings.baseUrl,
      ANANTA_PROFILE_ID: runtime.settings.profileId,
      ANANTA_AUTH_MODE: runtime.settings.authMode
    }
  });
  const launchCommand = [
    "python -m client_surfaces.tui_runtime.ananta_tui",
    `--base-url ${shellQuote(runtime.settings.baseUrl)}`,
    `--profile-id ${shellQuote(runtime.settings.profileId)}`,
    `--auth-mode ${shellQuote(runtime.settings.authMode)}`
  ].join(" ");
  terminal.show(true);
  terminal.sendText(launchCommand, true);
  void vscode.window.showInformationMessage("Ananta TUI launched in integrated terminal.");
}

export async function checkHealthCmd(refreshSidebarData: (source: string) => Promise<RuntimeClientContext | null>): Promise<void> {
  const runtime = await refreshSidebarData("check_health");
  if (!runtime) return;
  const healthy = capabilityRef.current.workflowAvailability["ananta.submitGoal"];
  if (healthy) {
    void vscode.window.showInformationMessage("Ananta runtime health/capabilities were refreshed.");
  } else {
    void vscode.window.showWarningMessage("Ananta runtime refreshed with degraded capability state.");
  }
}

export async function openWebFallbackInternal(runtime: RuntimeClientContext, args?: WebFallbackArgs): Promise<boolean> {
  const providedTarget = args?.target;
  let target = providedTarget;
  if (!target) {
    const picked = await vscode.window.showQuickPick(
      (["tasks", "artifacts", "audit", "config", "repair"] as WebFallbackTarget[]).map((value) => ({
        label: fallbackTargetLabel(value),
        target: value
      })),
      {
        title: "Ananta browser fallback",
        placeHolder: "Select the page to open in browser fallback"
      }
    );
    target = picked?.target;
  }
  if (!target) {
    return false;
  }
  const url = buildWebFallbackUrl(runtime.settings.baseUrl, target, args?.id, args?.traceId);
  if (!url) {
    void vscode.window.showWarningMessage("Ananta browser fallback unavailable: configure a valid HTTP(S) ananta.baseUrl.");
    return false;
  }
  await vscode.env.openExternal(vscode.Uri.parse(url));
  const source = args?.source ? ` (${args.source})` : "";
  void vscode.window.showInformationMessage(`Opened Ananta ${fallbackTargetLabel(target)} browser fallback${source}.`);
  return true;
}
