import * as vscode from "vscode";
import { AnantaBackendClient, WorkflowRequestMetadata } from "./runtime/backendClient";
import {
  buildCapabilitySnapshot,
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
import { AnantaStatusTreeProvider } from "./views/statusTreeProvider";

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
  projectEvolve: "ananta.projectEvolve"
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

function extractTaskId(data: unknown): string {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return "";
  }
  const value = (data as Record<string, unknown>).task_id;
  if (typeof value !== "string") {
    return "";
  }
  return value.trim();
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

async function applyCapabilityContexts(values: Record<WorkflowCommandId, boolean>): Promise<void> {
  await Promise.all(
    WORKFLOW_COMMANDS.map((commandId) => vscode.commands.executeCommand("setContext", toCommandContextKey(commandId), values[commandId]))
  );
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
  const diagnostics = vscode.languages.createDiagnosticCollection("ananta-runtime");
  const diagnosticsUri = vscode.Uri.parse("ananta:/runtime/status");
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = COMMANDS.openStatusView;
  statusBar.text = "$(circle-outline) Ananta Idle";
  statusBar.tooltip = "Ananta runtime status";
  statusBar.show();

  context.subscriptions.push(output, diagnostics, statusBar);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.statusView", statusView));

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

  async function refreshCapabilities(runtime: RuntimeClientContext): Promise<Record<WorkflowCommandId, boolean>> {
    const capabilities = await runtime.client.getCapabilities();
    const snapshot = buildCapabilitySnapshot(capabilities);
    const commandAvailability = workflowDefaultState();
    const details: string[] = [`capabilities_status=${capabilities.statusCode ?? "none"}`];
    for (const commandId of WORKFLOW_COMMANDS) {
      const gate = evaluateWorkflowCommand(snapshot, commandId);
      commandAvailability[commandId] = gate.allowed;
      if (!gate.allowed) {
        details.push(`${commandId}=${gate.reason}`);
      }
    }
    await applyCapabilityContexts(commandAvailability);
    setRuntimeUi("configured", capabilities.state, runtime.settings.baseUrl, runtime.settings.profileId, details);
    return commandAvailability;
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
        placeHolder: "Select goal mode (optional mode selection)"
      }
    );
    return picked?.workflow ?? null;
  }

  async function executeWorkflowCommand(commandId: WorkflowCommandId): Promise<void> {
    const runtime = await buildRuntimeClient(context, statusView, output);
    if (!runtime) {
      await applyCapabilityContexts(workflowDefaultState());
      return;
    }

    const availability = await refreshCapabilities(runtime);
    const effectiveCommand = commandId === COMMANDS.submitGoal ? await chooseQuickGoalMode() : commandId;
    if (!effectiveCommand) {
      return;
    }
    const workflow = WORKFLOW_DEFINITIONS[effectiveCommand];
    if (!availability[effectiveCommand]) {
      const requiredCapability = evaluateWorkflowCommand(
        buildCapabilitySnapshot(await runtime.client.getCapabilities()),
        effectiveCommand
      );
      const reason = requiredCapability.reason;
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
    const action = await vscode.window.showInformationMessage(message, "Open Result", "Open Status");
    if (action === "Open Result") {
      await openResultLink(links);
    } else if (action === "Open Status") {
      await vscode.commands.executeCommand(COMMANDS.openStatusView);
    }
  }

  await applyCapabilityContexts(workflowDefaultState());

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
      const runtime = await buildRuntimeClient(context, statusView, output);
      if (!runtime) {
        await applyCapabilityContexts(workflowDefaultState());
        return;
      }
      try {
        const health = await runtime.client.getHealth();
        const capabilities = await runtime.client.getCapabilities();
        const capabilitySnapshot = buildCapabilitySnapshot(capabilities);
        const availability = workflowDefaultState();
        for (const commandId of WORKFLOW_COMMANDS) {
          availability[commandId] = evaluateWorkflowCommand(capabilitySnapshot, commandId).allowed;
        }
        await applyCapabilityContexts(availability);
        setRuntimeUi(
          health.state,
          capabilities.state,
          runtime.settings.baseUrl,
          runtime.settings.profileId,
          [`health_status=${health.statusCode ?? "none"}`, `capabilities_status=${capabilities.statusCode ?? "none"}`]
        );
        output.appendLine(
          `[health] state=${health.state} status=${health.statusCode ?? "none"} capabilities_state=${capabilities.state}`
        );
        if (health.ok && capabilities.ok) {
          void vscode.window.showInformationMessage("Ananta backend is healthy and capabilities were loaded.");
        } else {
          void vscode.window.showWarningMessage(`Ananta degraded: health=${health.state}, capabilities=${capabilities.state}`);
        }
      } catch (error) {
        const safeError = sanitizeErrorMessage(error);
        output.appendLine(`[health] failed=${safeError}`);
        setRuntimeUi("backend_unreachable", "unknown", runtime.settings.baseUrl, runtime.settings.profileId, [safeError]);
        await applyCapabilityContexts(workflowDefaultState());
        void vscode.window.showErrorMessage(`Ananta check failed: ${safeError}`);
      }
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
      await applyCapabilityContexts(workflowDefaultState());
      setRuntimeUi("configured", "unknown", String(vscode.workspace.getConfiguration("ananta").get("baseUrl", "-")), String(vscode.workspace.getConfiguration("ananta").get("profileId", "-")), ["configuration_changed"]);
    })
  );
}

export function deactivate(): void {
  // no-op
}
