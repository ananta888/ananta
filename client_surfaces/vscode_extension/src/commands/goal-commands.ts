import * as vscode from "vscode";
import { AnantaBackendClient, WorkflowRequestMetadata } from "../runtime/backendClient";
import { WorkflowCommandId, WORKFLOW_COMMANDS } from "../runtime/capabilityGate";
import { packageEditorContext, RawEditorContextInput } from "../runtime/contextCapture";
import { sanitizeErrorMessage } from "../runtime/redaction";
import { buildResultLinks } from "../runtime/resultLinks";
import { GoalTaskRef, ArtifactRef } from "../views/sidebarProviders";
import {
  COMMANDS,
  RuntimeClientContext,
  capabilityRef,
  readString,
  extractTaskId,
  extractArtifactId,
  extractGoalId,
  openJsonPanel,
  openWebFallbackInternal
} from "./helpers";

export interface WorkflowDefinition {
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

export const WORKFLOW_DEFINITIONS: Record<WorkflowCommandId, WorkflowDefinition> = {
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

export function readActiveEditorContext(): RawEditorContextInput {
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

export function buildPreviewDetail(preview: ReturnType<typeof packageEditorContext>["preview"]): string {
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

export async function confirmContextPreview(
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

export async function promptGoalText(workflow: WorkflowDefinition): Promise<string | null> {
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

export async function chooseQuickGoalMode(): Promise<WorkflowCommandId | null> {
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

export async function openGoalOrTaskDetailInternal(runtime: RuntimeClientContext, ref: GoalTaskRef): Promise<void> {
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
    await openGoalOrTaskDetailInternal(runtime, { kind: "task", id: artifactId });
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

export async function executeWorkflowCommand(
  commandId: WorkflowCommandId,
  refreshSidebarData: (source: string) => Promise<RuntimeClientContext | null>,
  setRuntimeUi: (connectionState: string, capabilitiesState: string, endpoint: string, profileId: string, details: string[]) => void
): Promise<void> {
  const runtime = await refreshSidebarData("workflow_preflight");
  if (!runtime) {
    return;
  }

  const effectiveCommand = commandId === COMMANDS.submitGoal ? await chooseQuickGoalMode() : commandId;
  if (!effectiveCommand) {
    return;
  }
  const workflow = WORKFLOW_DEFINITIONS[effectiveCommand];
  if (!capabilityRef.current.workflowAvailability[effectiveCommand]) {
    const reason = `command_not_available:${effectiveCommand}`;
    console.log(`[capability] denied command=${effectiveCommand} reason=${reason}`);
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
    console.log(`[workflow] failed command=${workflow.id} reason=${reason}`);
    void vscode.window.showWarningMessage(`Ananta degraded (${workflow.title}): ${reason}`);
    return;
  }

  const taskId = extractTaskId(response.data);
  const message = taskId
    ? `Ananta accepted ${workflow.title}. task_id=${taskId}`
    : `Ananta accepted ${workflow.title}.`;
  const links = buildResultLinks(runtime.settings.baseUrl, response.data);
  console.log(`[workflow] success command=${workflow.id} task_id=${taskId || "none"}`);
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
