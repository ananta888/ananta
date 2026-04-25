import * as vscode from "vscode";
import { AnantaBackendClient, WorkflowRequestMetadata } from "./runtime/backendClient";
import {
  buildCapabilitySnapshot,
  evaluateCapabilityAction,
  evaluateWorkflowCommand,
  toCommandContextKey,
  WorkflowCommandId,
  WORKFLOW_COMMANDS
} from "./runtime/capabilityGate";
import { packageEditorContext, RawEditorContextInput } from "./runtime/contextCapture";
import { redactSensitiveText, sanitizeErrorMessage } from "./runtime/redaction";
import { buildResultLinks } from "./runtime/resultLinks";
import { AnantaSecretStore } from "./runtime/secretStore";
import { resolveRuntimeSettings } from "./runtime/settings";
import { RuntimeSettings } from "./runtime/types";
import { buildWebFallbackUrl, fallbackTargetLabel, WebFallbackTarget } from "./runtime/webFallback";
import { AnantaStatusTreeProvider } from "./views/statusTreeProvider";
import {
  AuditRef,
  AuditTreeProvider,
  ApprovalQueueTreeProvider,
  ApprovalRef,
  ArtifactsTreeProvider,
  ArtifactRef,
  GoalsTasksTreeProvider,
  GoalTaskRef,
  RepairRef,
  RepairTreeProvider,
  RuntimeOverviewTreeProvider
} from "./views/sidebarProviders";

const COMMANDS = {
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

const APPROVAL_CONTEXT_KEYS = {
  approve: "ananta.capability.approvalApprove",
  reject: "ananta.capability.approvalReject"
} as const;

interface RuntimeClientContext {
  client: AnantaBackendClient;
  settings: RuntimeSettings;
}

interface WorkflowDefinition {
  id: WorkflowCommandId;
  operationPreset: string;
  title: string;
  requiresGoalInput: boolean;
  requiresSelection: boolean;
  defaultGoalText: string;
  run: (
    client: AnantaBackendClient,
    contextPayload: object,
    goalText: string,
    metadata: WorkflowRequestMetadata
  ) => Promise<unknown>;
}

interface CapabilityExecutionState {
  workflowAvailability: Record<WorkflowCommandId, boolean>;
  approvalActions: {
    approve: boolean;
    reject: boolean;
  };
}

const WORKFLOW_DEFINITIONS: Record<WorkflowCommandId, WorkflowDefinition> = {
  "ananta.submitGoal": {
    id: "ananta.submitGoal",
    operationPreset: "goal_submit",
    title: "Submit Goal",
    requiresGoalInput: true,
    requiresSelection: false,
    defaultGoalText: "Deliver requested change safely",
    run: (client, contextPayload, goalText, metadata) => client.submitGoal(goalText, contextPayload, metadata)
  },
  "ananta.analyzeSelection": {
    id: "ananta.analyzeSelection",
    operationPreset: "analyze",
    title: "Analyze Selection",
    requiresGoalInput: false,
    requiresSelection: true,
    defaultGoalText: "Analyze current editor selection",
    run: (client, contextPayload, goalText, metadata) => client.analyzeContext(contextPayload, metadata, goalText)
  },
  "ananta.reviewFile": {
    id: "ananta.reviewFile",
    operationPreset: "review",
    title: "Review File",
    requiresGoalInput: false,
    requiresSelection: false,
    defaultGoalText: "Review current file context",
    run: (client, contextPayload, goalText, metadata) => client.reviewContext(contextPayload, metadata, goalText)
  },
  "ananta.patchPlan": {
    id: "ananta.patchPlan",
    operationPreset: "patch_plan",
    title: "Patch Plan",
    requiresGoalInput: true,
    requiresSelection: false,
    defaultGoalText: "Create patch plan for current context",
    run: (client, contextPayload, goalText, metadata) => client.patchPlan(contextPayload, metadata, goalText)
  },
  "ananta.projectNew": {
    id: "ananta.projectNew",
    operationPreset: "project_new",
    title: "Project New",
    requiresGoalInput: true,
    requiresSelection: false,
    defaultGoalText: "Create a new software project",
    run: (client, contextPayload, goalText, metadata) => client.createProjectNew(goalText, contextPayload, metadata)
  },
  "ananta.projectEvolve": {
    id: "ananta.projectEvolve",
    operationPreset: "project_evolve",
    title: "Project Evolve",
    requiresGoalInput: true,
    requiresSelection: false,
    defaultGoalText: "Evolve an existing software project",
    run: (client, contextPayload, goalText, metadata) => client.createProjectEvolve(goalText, contextPayload, metadata)
  }
};

const QUICK_GOAL_MODES: Array<{ label: string; description: string; workflow: WorkflowCommandId }> = [
  {
    label: "Submit Goal",
    description: "Create a normal goal task flow",
    workflow: "ananta.submitGoal"
  },
  {
    label: "Patch Plan",
    description: "Create patch planning task flow",
    workflow: "ananta.patchPlan"
  },
  {
    label: "Project New",
    description: "Create new project task flow",
    workflow: "ananta.projectNew"
  },
  {
    label: "Project Evolve",
    description: "Create project evolution task flow",
    workflow: "ananta.projectEvolve"
  }
];

function workflowDefaultState(): Record<WorkflowCommandId, boolean> {
  return WORKFLOW_COMMANDS.reduce(
    (acc, commandId) => {
      acc[commandId] = false;
      return acc;
    },
    {} as Record<WorkflowCommandId, boolean>
  );
}

function defaultCapabilityState(): CapabilityExecutionState {
  return {
    workflowAvailability: workflowDefaultState(),
    approvalActions: {
      approve: false,
      reject: false
    }
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readString(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return "";
}

function readItems(payload: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(payload)) {
    return payload.map((entry) => asRecord(entry)).filter((entry): entry is Record<string, unknown> => entry !== null);
  }
  const record = asRecord(payload);
  if (!record || !Array.isArray(record.items)) {
    return [];
  }
  return record.items.map((entry) => asRecord(entry)).filter((entry): entry is Record<string, unknown> => entry !== null);
}

function redactSensitiveValue(value: unknown): unknown {
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

function firstRecord(payload: unknown): Record<string, unknown> | null {
  const items = readItems(payload);
  if (items.length > 0) {
    return items[0];
  }
  return asRecord(payload);
}

function extractProviderSummary(providerPayload: unknown, catalogPayload: unknown): string {
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

function extractModelSummary(benchmarksPayload: unknown): string {
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

function extractGovernanceSummary(
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

function extractTaskId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "task_id");
}

function extractGoalId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "goal_id");
}

function extractArtifactId(data: unknown): string {
  const parsed = asRecord(data);
  if (!parsed) {
    return "";
  }
  return readString(parsed, "artifact_id");
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function shellQuote(value: string): string {
  return "'" + String(value || "").replace(/'/g, "'\\''") + "'";
}

function openJsonPanel(title: string, payload: unknown): void {
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

function statusBarText(connectionState: string, capabilitiesState: string): string {
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

function diagnosticSeverity(state: string): vscode.DiagnosticSeverity {
  if (state === "capability_missing" || state === "policy_denied") {
    return vscode.DiagnosticSeverity.Warning;
  }
  return vscode.DiagnosticSeverity.Error;
}

interface WebFallbackArgs {
  target?: WebFallbackTarget;
  id?: string;
  traceId?: string;
  source?: string;
}

function readActiveEditorContext(): RawEditorContextInput {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return {
      filePath: null,
      projectRoot: null,
      languageId: null,
      selectionText: null,
      fileContentExcerpt: null
    };
  }
  const doc = editor.document;
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(doc.uri) ?? vscode.workspace.workspaceFolders?.[0];
  const lineEnd = Math.max(0, Math.min(doc.lineCount - 1, 120));
  const endCharacter = doc.lineAt(lineEnd).range.end.character;
  const excerptRange = new vscode.Range(0, 0, lineEnd, endCharacter);
  return {
    filePath: doc.uri.scheme === "file" ? doc.uri.fsPath : doc.uri.toString(),
    projectRoot: workspaceFolder?.uri.fsPath ?? null,
    languageId: doc.languageId,
    selectionText: editor.selection.isEmpty ? "" : doc.getText(editor.selection),
    fileContentExcerpt: doc.getText(excerptRange)
  };
}

function buildPreviewDetail(preview: ReturnType<typeof packageEditorContext>["preview"]): string {
  const lines = [
    `File: ${preview.filePath ?? "-"}`,
    `Project: ${preview.projectRoot ?? "-"}`,
    `Language: ${preview.languageId ?? "-"}`,
    `Selection chars: ${preview.selectionLength}`,
    `Selection clipped: ${preview.selectionClipped}`,
    `Excerpt clipped: ${preview.fileContentClipped}`,
    `Warnings: ${preview.warnings.length > 0 ? preview.warnings.join(", ") : "-"}`,
    `Selection excerpt: ${preview.selectionExcerpt ?? "-"}`,
    `File excerpt: ${preview.fileExcerpt ?? "-"}`
  ];
  if (preview.blockedReasons.length > 0) {
    lines.push(`Blocked: ${preview.blockedReasons.join(", ")}`);
  }
  return lines.join("\n");
}

function isTextArtifact(payload: Record<string, unknown>): boolean {
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

async function applyWorkflowContexts(values: Record<WorkflowCommandId, boolean>): Promise<void> {
  await Promise.all(
    WORKFLOW_COMMANDS.map((commandId) => vscode.commands.executeCommand("setContext", toCommandContextKey(commandId), values[commandId]))
  );
}

async function applyApprovalActionContexts(approve: boolean, reject: boolean): Promise<void> {
  await Promise.all([
    vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.approve, approve),
    vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.reject, reject)
  ]);
}

async function buildRuntimeClient(
  context: vscode.ExtensionContext,
  statusView: AnantaStatusTreeProvider,
  output: vscode.OutputChannel
): Promise<RuntimeClientContext | null> {
  const config = vscode.workspace.getConfiguration("ananta");
  const secretStore = new AnantaSecretStore(context.secrets);
  const resolved = await resolveRuntimeSettings(config, secretStore);
  if (!resolved.settings) {
    const message = `Ananta settings invalid: ${resolved.validationErrors.join(", ")}`;
    statusView.setSnapshot({
      connectionState: "invalid_config",
      capabilitiesState: "unknown",
      endpoint: String(config.get("baseUrl", "-")),
      profileId: String(config.get("profileId", "-")),
      details: resolved.validationErrors
    });
    output.appendLine(`[runtime] ${redactSensitiveText(message)}`);
    void vscode.window.showWarningMessage(message);
    return null;
  }
  return {
    client: new AnantaBackendClient(resolved.settings),
    settings: resolved.settings
  };
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel("Ananta");
  const statusView = new AnantaStatusTreeProvider();
  const goalsTasksView = new GoalsTasksTreeProvider();
  const artifactsView = new ArtifactsTreeProvider();
  const approvalsView = new ApprovalQueueTreeProvider();
  const auditView = new AuditTreeProvider();
  const repairView = new RepairTreeProvider();
  const runtimeView = new RuntimeOverviewTreeProvider();
  const diagnostics = vscode.languages.createDiagnosticCollection("ananta-runtime");
  const diagnosticsUri = vscode.Uri.parse("ananta:/runtime/status");
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = COMMANDS.openStatusView;
  statusBar.text = "$(circle-outline) Ananta Idle";
  statusBar.tooltip = "Ananta runtime status";
  statusBar.show();

  let capabilityState = defaultCapabilityState();

  context.subscriptions.push(output, diagnostics, statusBar);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.statusView", statusView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.goalsTasksView", goalsTasksView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.artifactsView", artifactsView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.approvalsView", approvalsView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.auditView", auditView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.repairView", repairView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.runtimeView", runtimeView));

  const setRuntimeUi = (
    connectionState: string,
    capabilitiesState: string,
    endpoint: string,
    profileId: string,
    details: string[]
  ): void => {
    statusView.setSnapshot({
      connectionState,
      capabilitiesState,
      endpoint,
      profileId,
      details
    });
    statusBar.text = statusBarText(connectionState, capabilitiesState);
    statusBar.tooltip = [`Endpoint: ${endpoint}`, `Profile: ${profileId}`, ...details].join("\n");

    if (connectionState === "healthy" && capabilitiesState === "healthy") {
      diagnostics.delete(diagnosticsUri);
      return;
    }
    const entries = [
      new vscode.Diagnostic(
        new vscode.Range(0, 0, 0, 1),
        `Ananta connection state: ${connectionState}`,
        diagnosticSeverity(connectionState)
      ),
      new vscode.Diagnostic(
        new vscode.Range(0, 0, 0, 1),
        `Ananta capability state: ${capabilitiesState}`,
        diagnosticSeverity(capabilitiesState)
      )
    ];
    for (const detail of details) {
      entries.push(new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), detail, vscode.DiagnosticSeverity.Information));
    }
    diagnostics.set(diagnosticsUri, entries);
  };

  const setRuntimeOverview = (
    runtime: RuntimeClientContext | null,
    connectionState: string,
    capabilitiesState: string,
    counts: { goals: number; tasks: number; artifacts: number; approvals: number; audits: number; repairs: number },
    details: string[],
    activeProfileId?: string
  ): void => {
    runtimeView.setSnapshot({
      connectionState,
      capabilitiesState,
      endpoint: runtime?.settings.baseUrl ?? "-",
      profileId: activeProfileId || runtime?.settings.profileId || "-",
      goalCount: counts.goals,
      taskCount: counts.tasks,
      artifactCount: counts.artifacts,
      approvalCount: counts.approvals,
      auditCount: counts.audits,
      repairCount: counts.repairs,
      filterStatus: goalsTasksView.getFilter(),
      details
    });
  };

  const resetSidebarViews = (reason: string): void => {
    goalsTasksView.setData([], [], reason);
    artifactsView.setData([], reason);
    approvalsView.setData([], reason);
    auditView.setData([], reason);
    repairView.setData([], reason);
    setRuntimeOverview(
      null,
      "invalid_config",
      "unknown",
      { goals: 0, tasks: 0, artifacts: 0, approvals: 0, audits: 0, repairs: 0 },
      [reason],
      undefined
    );
  };

  async function refreshSidebarData(source: string): Promise<RuntimeClientContext | null> {
    const runtime = await buildRuntimeClient(context, statusView, output);
    if (!runtime) {
      capabilityState = defaultCapabilityState();
      await applyWorkflowContexts(capabilityState.workflowAvailability);
      await applyApprovalActionContexts(false, false);
      resetSidebarViews("runtime_settings_invalid");
      return null;
    }

    try {
      const [health, capabilities, goals, tasks, artifacts, approvals, audits, repairs, dashboard, assistant, providers, providerCatalog, benchmarks, config] = await Promise.all([
        runtime.client.getHealth(),
        runtime.client.getCapabilities(),
        runtime.client.listGoals(),
        runtime.client.listTasks(),
        runtime.client.listArtifacts(),
        runtime.client.listApprovals(),
        runtime.client.getAuditLogs(30, 0),
        runtime.client.listRepairs(),
        runtime.client.getDashboardReadModel(),
        runtime.client.getAssistantReadModel(),
        runtime.client.listProviders(),
        runtime.client.listProviderCatalog(),
        runtime.client.getLlmBenchmarks("analysis", 3),
        runtime.client.getConfig()
      ]);

      const capabilitySnapshot = buildCapabilitySnapshot(capabilities);
      const workflowAvailability = workflowDefaultState();
      for (const commandId of WORKFLOW_COMMANDS) {
        workflowAvailability[commandId] = evaluateWorkflowCommand(capabilitySnapshot, commandId).allowed;
      }
      const approvalApprove = evaluateCapabilityAction(capabilitySnapshot, {
        actionId: COMMANDS.approveApproval,
        requiredCapability: "approvals",
        actionAliases: ["approvals.approve", "approve_approval"]
      }).allowed;
      const approvalReject = evaluateCapabilityAction(capabilitySnapshot, {
        actionId: COMMANDS.rejectApproval,
        requiredCapability: "approvals",
        actionAliases: ["approvals.reject", "reject_approval"]
      }).allowed;

      capabilityState = {
        workflowAvailability,
        approvalActions: {
          approve: approvalApprove,
          reject: approvalReject
        }
      };
      await applyWorkflowContexts(workflowAvailability);
      await applyApprovalActionContexts(approvalApprove, approvalReject);

      goalsTasksView.setData(goals.data, tasks.data, goals.ok && tasks.ok ? "" : `goals=${goals.state}, tasks=${tasks.state}`);
      artifactsView.setData(artifacts.data, artifacts.ok ? "" : `artifacts=${artifacts.state}`);
      approvalsView.setData(approvals.data, approvals.ok ? "" : `approvals=${approvals.state}`);
      const redactedAudits = readItems(audits.data).map((entry) => redactSensitiveValue(entry) as Record<string, unknown>);
      const redactedRepairs = readItems(repairs.data).map((entry) => redactSensitiveValue(entry) as Record<string, unknown>);
      auditView.setData(redactedAudits, audits.ok ? "" : `audit=${audits.state}`);
      repairView.setData(redactedRepairs, repairs.ok ? "" : `repairs=${repairs.state}`);

      const goalCount = readItems(goals.data).length;
      const taskCount = readItems(tasks.data).length;
      const artifactCount = readItems(artifacts.data).length;
      const approvalCount = readItems(approvals.data).length;
      const auditCount = redactedAudits.length;
      const repairCount = redactedRepairs.length;
      const dashboardSnapshot = firstRecord(dashboard.data);
      const activeProfile =
        (dashboardSnapshot ? readString(dashboardSnapshot, "active_profile_id", "profile_id", "active_profile") : "") ||
        runtime.settings.profileId;
      const providerSummary = extractProviderSummary(providers.data, providerCatalog.data);
      const modelSummary = extractModelSummary(benchmarks.data);
      const governanceSummary = extractGovernanceSummary(assistant.data, config.data, capabilities.state, health.state);

      const details = [
        `refresh_source=${source}`,
        `health_status=${health.statusCode ?? "none"}`,
        `capabilities_status=${capabilities.statusCode ?? "none"}`,
        `audit_status=${audits.statusCode ?? "none"}(${audits.state})`,
        `repair_status=${repairs.statusCode ?? "none"}(${repairs.state})`,
        `active_profile=${activeProfile}`,
        governanceSummary,
        providerSummary,
        modelSummary
      ];
      setRuntimeUi(health.state, capabilities.state, runtime.settings.baseUrl, activeProfile, details);
      setRuntimeOverview(
        runtime,
        health.state,
        capabilities.state,
        {
          goals: goalCount,
          tasks: taskCount,
          artifacts: artifactCount,
          approvals: approvalCount,
          audits: auditCount,
          repairs: repairCount
        },
        details,
        activeProfile
      );
      return runtime;
    } catch (error) {
      const safeError = sanitizeErrorMessage(error);
      output.appendLine(`[sidebar] refresh failed=${safeError}`);
      capabilityState = defaultCapabilityState();
      await applyWorkflowContexts(capabilityState.workflowAvailability);
      await applyApprovalActionContexts(false, false);
      setRuntimeUi("backend_unreachable", "unknown", runtime.settings.baseUrl, runtime.settings.profileId, [safeError]);
      resetSidebarViews(`backend_unreachable:${safeError}`);
      return runtime;
    }
  }

  async function openResultLink(links: ReturnType<typeof buildResultLinks>): Promise<void> {
    if (links.length === 0) {
      return;
    }
    const picked = await vscode.window.showQuickPick(
      links.map((link) => ({ label: link.label, description: link.url, link })),
      {
        title: "Open Ananta result",
        placeHolder: "Select result link to open in browser"
      }
    );
    if (!picked) {
      return;
    }
    await vscode.env.openExternal(vscode.Uri.parse(picked.link.url));
  }

  async function openWebFallbackInternal(runtime: RuntimeClientContext, args?: WebFallbackArgs): Promise<boolean> {
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

  async function confirmContextPreview(
    workflow: WorkflowDefinition,
    preview: ReturnType<typeof packageEditorContext>["preview"]
  ): Promise<boolean> {
    if (preview.blockedReasons.length > 0) {
      const detail = buildPreviewDetail(preview);
      await vscode.window.showErrorMessage(
        `${workflow.title} blocked due to high-risk secret detection (${preview.blockedReasons.join(", ")}).`,
        { modal: true, detail }
      );
      return false;
    }
    const detail = buildPreviewDetail(preview);
    if (preview.warnings.length > 0) {
      const choice = await vscode.window.showWarningMessage(
        `${workflow.title} context has warnings. Submit redacted payload?`,
        { modal: true, detail },
        "Submit",
        "Cancel"
      );
      return choice === "Submit";
    }
    const choice = await vscode.window.showInformationMessage(
      `${workflow.title}: review context preview before sending.`,
      { modal: true, detail },
      "Submit",
      "Cancel"
    );
    return choice === "Submit";
  }

  async function promptGoalText(workflow: WorkflowDefinition): Promise<string | null> {
    if (!workflow.requiresGoalInput) {
      return workflow.defaultGoalText;
    }
    return (
      (await vscode.window.showInputBox({
        title: `Ananta ${workflow.title}`,
        prompt: "Goal text",
        value: workflow.defaultGoalText,
        ignoreFocusOut: true,
        validateInput(value: string): string | null {
          const normalized = String(value || "").trim();
          if (normalized.length === 0) {
            return "Goal text must not be empty.";
          }
          if (normalized.length > 1200) {
            return "Goal text must be <= 1200 characters.";
          }
          return null;
        }
      })) ?? null
    );
  }

  async function chooseQuickGoalMode(): Promise<WorkflowCommandId | null> {
    const picked = await vscode.window.showQuickPick(
      QUICK_GOAL_MODES.map((entry) => ({
        label: entry.label,
        description: entry.description,
        workflow: entry.workflow
      })),
      {
        title: "Ananta Goal Mode",
        placeHolder: "Select goal mode"
      }
    );
    return picked?.workflow ?? null;
  }

  async function openGoalOrTaskDetailInternal(runtime: RuntimeClientContext, ref: GoalTaskRef): Promise<void> {
    if (ref.kind === "goal") {
      const goal = await runtime.client.getGoal(ref.id);
      if (!goal.ok) {
        await openWebFallbackInternal(runtime, { target: "goals", id: ref.id, source: "goal_detail_degraded" });
        void vscode.window.showWarningMessage(`Goal detail degraded (${goal.state}); browser fallback opened.`);
        return;
      }
      openJsonPanel(`Ananta Goal ${ref.id}`, {
        schema: "vscode_goal_detail_v1",
        goal_id: ref.id,
        detail: goal.data
      });
      return;
    }

    const [task, logs] = await Promise.all([runtime.client.getTask(ref.id), runtime.client.getTaskLogs(ref.id)]);
    const detailPayload = {
      schema: "vscode_task_detail_v1",
      task_id: ref.id,
      task_state: task.state,
      task_status_code: task.statusCode,
      task_missing: !task.ok,
      task_detail: task.data,
      logs_state: logs.state,
      logs_status_code: logs.statusCode,
      logs_available: logs.ok,
      logs_payload: logs.data,
      stale_or_missing_state_explicit: !task.ok || !logs.ok
    };
    openJsonPanel(`Ananta Task ${ref.id}`, detailPayload);
  }

  async function openArtifactDetailInternal(runtime: RuntimeClientContext, ref: ArtifactRef): Promise<void> {
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

  async function openApprovalDetailInternal(runtime: RuntimeClientContext, ref: ApprovalRef): Promise<void> {
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

  async function openAuditDetailInternal(runtime: RuntimeClientContext, ref: AuditRef): Promise<void> {
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

  async function openRepairDetailInternal(runtime: RuntimeClientContext, ref: RepairRef): Promise<void> {
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

  async function renderWorkflowResult(
    runtime: RuntimeClientContext,
    workflowTitle: string,
    data: unknown
  ): Promise<void> {
    const taskId = extractTaskId(data);
    if (taskId) {
      await openGoalOrTaskDetailInternal(runtime, { kind: "task", id: taskId });
      return;
    }
    const artifactId = extractArtifactId(data);
    if (artifactId) {
      await openArtifactDetailInternal(runtime, { id: artifactId });
      return;
    }
    const goalId = extractGoalId(data);
    if (goalId) {
      await openGoalOrTaskDetailInternal(runtime, { kind: "goal", id: goalId });
      return;
    }
    openJsonPanel(`Ananta ${workflowTitle} Result`, {
      schema: "vscode_result_fallback_v1",
      note: "Unsupported result type; using text/browser fallback.",
      payload: data,
      browser_links: buildResultLinks(runtime.settings.baseUrl, data)
    });
  }

  async function executeWorkflowCommand(commandId: WorkflowCommandId): Promise<void> {
    const runtime = await refreshSidebarData("workflow_preflight");
    if (!runtime) {
      return;
    }

    const effectiveCommand = commandId === COMMANDS.submitGoal ? await chooseQuickGoalMode() : commandId;
    if (!effectiveCommand) {
      return;
    }
    const workflow = WORKFLOW_DEFINITIONS[effectiveCommand];
    if (!capabilityState.workflowAvailability[effectiveCommand]) {
      const reason = `command_not_available:${effectiveCommand}`;
      output.appendLine(`[capability] denied command=${effectiveCommand} reason=${reason}`);
      void vscode.window.showWarningMessage(`Ananta command denied (${reason}).`);
      return;
    }

    const rawContext = readActiveEditorContext();
    const packaged = packageEditorContext(rawContext);
    if (workflow.requiresSelection && packaged.preview.selectionLength === 0) {
      void vscode.window.showWarningMessage(`${workflow.title} requires an active text selection.`);
      return;
    }

    const goalText = await promptGoalText(workflow);
    if (goalText === null) {
      return;
    }
    if (!(await confirmContextPreview(workflow, packaged.preview))) {
      return;
    }

    const metadata: WorkflowRequestMetadata = {
      operationPreset: workflow.operationPreset,
      commandId: workflow.id,
      profileId: runtime.settings.profileId,
      runtimeTarget: runtime.settings.runtimeTarget,
      mode: workflow.operationPreset
    };

    const response = (await workflow.run(runtime.client, packaged.payload, goalText, metadata)) as {
      ok: boolean;
      state: string;
      statusCode: number | null;
      data: Record<string, unknown> | null;
      error: string | null;
    };
    const details = [
      `command=${workflow.id}`,
      `operation=${workflow.operationPreset}`,
      `status=${response.statusCode ?? "none"}`
    ];
    setRuntimeUi(response.state, response.state, runtime.settings.baseUrl, runtime.settings.profileId, details);

    if (!response.ok) {
      const reason = response.error ?? `request_failed:${response.state}`;
      output.appendLine(`[workflow] failed command=${workflow.id} reason=${reason}`);
      void vscode.window.showWarningMessage(`Ananta degraded (${workflow.title}): ${reason}`);
      return;
    }

    const taskId = extractTaskId(response.data);
    const message = taskId
      ? `Ananta accepted ${workflow.title}. task_id=${taskId}`
      : `Ananta accepted ${workflow.title}.`;
    const links = buildResultLinks(runtime.settings.baseUrl, response.data);
    output.appendLine(`[workflow] success command=${workflow.id} task_id=${taskId || "none"}`);
    const action = await vscode.window.showInformationMessage(message, "Open Detail", "Open Browser", "Open Status");
    if (action === "Open Detail") {
      await renderWorkflowResult(runtime, workflow.title, response.data);
    } else if (action === "Open Browser") {
      await openResultLink(links);
    } else if (action === "Open Status") {
      await vscode.commands.executeCommand(COMMANDS.openStatusView);
    }
    await refreshSidebarData("workflow_success");
  }

  async function runApprovalAction(ref: ApprovalRef, action: "approve" | "reject"): Promise<void> {
    const runtime = await refreshSidebarData(`approval_${action}_preflight`);
    if (!runtime) {
      return;
    }
    const allowed = action === "approve" ? capabilityState.approvalActions.approve : capabilityState.approvalActions.reject;
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

  await applyWorkflowContexts(workflowDefaultState());
  await applyApprovalActionContexts(false, false);

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.configureProfile, async () => {
      await vscode.commands.executeCommand("workbench.action.openSettings", "ananta.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openStatusView, async () => {
      await vscode.commands.executeCommand("workbench.view.extension.ananta");
      await vscode.commands.executeCommand("ananta.statusView.focus");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.refreshSidebarData, async () => {
      await refreshSidebarData("manual_refresh");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openWebFallback, async (args?: WebFallbackArgs) => {
      const runtime = await refreshSidebarData("open_web_fallback");
      if (!runtime) {
        return;
      }
      await openWebFallbackInternal(runtime, args);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.launchTui, async () => {
      const runtime = await refreshSidebarData("launch_tui");
      if (!runtime) {
        return;
      }
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
      void vscode.window.showInformationMessage("Ananta TUI launched in integrated terminal (without passing token on CLI).");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.setGoalTaskStatusFilter, async () => {
      const statuses = goalsTasksView.availableStatuses();
      const picked = await vscode.window.showQuickPick(
        [{ label: "all", description: "Show all statuses" }, ...statuses.map((status) => ({ label: status }))],
        {
          title: "Filter Goals/Tasks by status",
          placeHolder: "Select status filter"
        }
      );
      if (!picked) {
        return;
      }
      goalsTasksView.setFilter(picked.label);
      runtimeView.refresh();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.storeToken, async () => {
      const value = await vscode.window.showInputBox({
        title: "Store Ananta Auth Token",
        prompt: "Enter token for current Ananta profile",
        password: true,
        ignoreFocusOut: true
      });
      if (!value) {
        return;
      }
      const config = vscode.workspace.getConfiguration("ananta");
      const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
      const secretStore = new AnantaSecretStore(context.secrets);
      await secretStore.storeToken(value, key);
      output.appendLine(`[auth] token stored with key=${key}`);
      void vscode.window.showInformationMessage("Ananta token stored in SecretStorage.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.clearToken, async () => {
      const config = vscode.workspace.getConfiguration("ananta");
      const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
      const secretStore = new AnantaSecretStore(context.secrets);
      await secretStore.clearToken(key);
      output.appendLine(`[auth] token cleared with key=${key}`);
      void vscode.window.showInformationMessage("Ananta token removed from SecretStorage.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.checkHealth, async () => {
      const runtime = await refreshSidebarData("check_health");
      if (!runtime) {
        return;
      }
      const healthy = capabilityState.workflowAvailability[COMMANDS.submitGoal];
      if (healthy) {
        void vscode.window.showInformationMessage("Ananta runtime health/capabilities were refreshed.");
      } else {
        void vscode.window.showWarningMessage("Ananta runtime refreshed with degraded capability state.");
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openGoalOrTaskDetail, async (ref?: GoalTaskRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No goal/task selected.");
        return;
      }
      const runtime = await refreshSidebarData("open_goal_task_detail");
      if (!runtime) {
        return;
      }
      await openGoalOrTaskDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openArtifactDetail, async (ref?: ArtifactRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No artifact selected.");
        return;
      }
      const runtime = await refreshSidebarData("open_artifact_detail");
      if (!runtime) {
        return;
      }
      await openArtifactDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openApprovalDetail, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No approval selected.");
        return;
      }
      const runtime = await refreshSidebarData("open_approval_detail");
      if (!runtime) {
        return;
      }
      await openApprovalDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openAuditDetail, async (ref?: AuditRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No audit entry selected.");
        return;
      }
      const runtime = await refreshSidebarData("open_audit_detail");
      if (!runtime) {
        return;
      }
      await openAuditDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openRepairDetail, async (ref?: RepairRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No repair session selected.");
        return;
      }
      const runtime = await refreshSidebarData("open_repair_detail");
      if (!runtime) {
        return;
      }
      await openRepairDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.approveApproval, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No approval selected for approve action.");
        return;
      }
      await runApprovalAction(ref, "approve");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.rejectApproval, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) {
        void vscode.window.showWarningMessage("No approval selected for reject action.");
        return;
      }
      await runApprovalAction(ref, "reject");
    })
  );

  for (const workflowCommand of WORKFLOW_COMMANDS) {
    context.subscriptions.push(
      vscode.commands.registerCommand(workflowCommand, async () => {
        await executeWorkflowCommand(workflowCommand);
      })
    );
  }

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (!event.affectsConfiguration("ananta")) {
        return;
      }
      capabilityState = defaultCapabilityState();
      await applyWorkflowContexts(capabilityState.workflowAvailability);
      await applyApprovalActionContexts(false, false);
      goalsTasksView.setFilter("all");
      await refreshSidebarData("configuration_changed");
    })
  );

  await refreshSidebarData("activate");
}

export function deactivate(): void {
  // no-op
}
