"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const backendClient_1 = require("./runtime/backendClient");
const capabilityGate_1 = require("./runtime/capabilityGate");
const contextCapture_1 = require("./runtime/contextCapture");
const redaction_1 = require("./runtime/redaction");
const resultLinks_1 = require("./runtime/resultLinks");
const secretStore_1 = require("./runtime/secretStore");
const settings_1 = require("./runtime/settings");
const statusTreeProvider_1 = require("./views/statusTreeProvider");
const sidebarProviders_1 = require("./views/sidebarProviders");
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
    approveApproval: "ananta.approveApproval",
    rejectApproval: "ananta.rejectApproval"
};
const APPROVAL_CONTEXT_KEYS = {
    approve: "ananta.capability.approvalApprove",
    reject: "ananta.capability.approvalReject"
};
const WORKFLOW_DEFINITIONS = {
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
const QUICK_GOAL_MODES = [
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
function workflowDefaultState() {
    return capabilityGate_1.WORKFLOW_COMMANDS.reduce((acc, commandId) => {
        acc[commandId] = false;
        return acc;
    }, {});
}
function defaultCapabilityState() {
    return {
        workflowAvailability: workflowDefaultState(),
        approvalActions: {
            approve: false,
            reject: false
        }
    };
}
function asRecord(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
    }
    return value;
}
function readString(record, ...keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim().length > 0) {
            return value.trim();
        }
    }
    return "";
}
function readItems(payload) {
    if (Array.isArray(payload)) {
        return payload.map((entry) => asRecord(entry)).filter((entry) => entry !== null);
    }
    const record = asRecord(payload);
    if (!record || !Array.isArray(record.items)) {
        return [];
    }
    return record.items.map((entry) => asRecord(entry)).filter((entry) => entry !== null);
}
function extractTaskId(data) {
    const parsed = asRecord(data);
    if (!parsed) {
        return "";
    }
    return readString(parsed, "task_id");
}
function extractGoalId(data) {
    const parsed = asRecord(data);
    if (!parsed) {
        return "";
    }
    return readString(parsed, "goal_id");
}
function extractArtifactId(data) {
    const parsed = asRecord(data);
    if (!parsed) {
        return "";
    }
    return readString(parsed, "artifact_id");
}
function escapeHtml(text) {
    return text
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}
function openJsonPanel(title, payload) {
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
function statusBarText(connectionState, capabilitiesState) {
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
function diagnosticSeverity(state) {
    if (state === "capability_missing" || state === "policy_denied") {
        return vscode.DiagnosticSeverity.Warning;
    }
    return vscode.DiagnosticSeverity.Error;
}
function readActiveEditorContext() {
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
function buildPreviewDetail(preview) {
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
function isTextArtifact(payload) {
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
    if (type.includes("binary") ||
        type.includes("image") ||
        type.includes("pdf") ||
        type.includes("zip") ||
        type.includes("audio") ||
        type.includes("video")) {
        return false;
    }
    return true;
}
async function applyWorkflowContexts(values) {
    await Promise.all(capabilityGate_1.WORKFLOW_COMMANDS.map((commandId) => vscode.commands.executeCommand("setContext", (0, capabilityGate_1.toCommandContextKey)(commandId), values[commandId])));
}
async function applyApprovalActionContexts(approve, reject) {
    await Promise.all([
        vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.approve, approve),
        vscode.commands.executeCommand("setContext", APPROVAL_CONTEXT_KEYS.reject, reject)
    ]);
}
async function buildRuntimeClient(context, statusView, output) {
    const config = vscode.workspace.getConfiguration("ananta");
    const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
    const resolved = await (0, settings_1.resolveRuntimeSettings)(config, secretStore);
    if (!resolved.settings) {
        const message = `Ananta settings invalid: ${resolved.validationErrors.join(", ")}`;
        statusView.setSnapshot({
            connectionState: "invalid_config",
            capabilitiesState: "unknown",
            endpoint: String(config.get("baseUrl", "-")),
            profileId: String(config.get("profileId", "-")),
            details: resolved.validationErrors
        });
        output.appendLine(`[runtime] ${(0, redaction_1.redactSensitiveText)(message)}`);
        void vscode.window.showWarningMessage(message);
        return null;
    }
    return {
        client: new backendClient_1.AnantaBackendClient(resolved.settings),
        settings: resolved.settings
    };
}
async function activate(context) {
    const output = vscode.window.createOutputChannel("Ananta");
    const statusView = new statusTreeProvider_1.AnantaStatusTreeProvider();
    const goalsTasksView = new sidebarProviders_1.GoalsTasksTreeProvider();
    const artifactsView = new sidebarProviders_1.ArtifactsTreeProvider();
    const approvalsView = new sidebarProviders_1.ApprovalQueueTreeProvider();
    const runtimeView = new sidebarProviders_1.RuntimeOverviewTreeProvider();
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
    context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.runtimeView", runtimeView));
    const setRuntimeUi = (connectionState, capabilitiesState, endpoint, profileId, details) => {
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
            new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), `Ananta connection state: ${connectionState}`, diagnosticSeverity(connectionState)),
            new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), `Ananta capability state: ${capabilitiesState}`, diagnosticSeverity(capabilitiesState))
        ];
        for (const detail of details) {
            entries.push(new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), detail, vscode.DiagnosticSeverity.Information));
        }
        diagnostics.set(diagnosticsUri, entries);
    };
    const setRuntimeOverview = (runtime, connectionState, capabilitiesState, counts, details) => {
        runtimeView.setSnapshot({
            connectionState,
            capabilitiesState,
            endpoint: runtime?.settings.baseUrl ?? "-",
            profileId: runtime?.settings.profileId ?? "-",
            goalCount: counts.goals,
            taskCount: counts.tasks,
            artifactCount: counts.artifacts,
            approvalCount: counts.approvals,
            filterStatus: goalsTasksView.getFilter(),
            details
        });
    };
    const resetSidebarViews = (reason) => {
        goalsTasksView.setData([], [], reason);
        artifactsView.setData([], reason);
        approvalsView.setData([], reason);
        setRuntimeOverview(null, "invalid_config", "unknown", { goals: 0, tasks: 0, artifacts: 0, approvals: 0 }, [reason]);
    };
    async function refreshSidebarData(source) {
        const runtime = await buildRuntimeClient(context, statusView, output);
        if (!runtime) {
            capabilityState = defaultCapabilityState();
            await applyWorkflowContexts(capabilityState.workflowAvailability);
            await applyApprovalActionContexts(false, false);
            resetSidebarViews("runtime_settings_invalid");
            return null;
        }
        try {
            const [health, capabilities, goals, tasks, artifacts, approvals] = await Promise.all([
                runtime.client.getHealth(),
                runtime.client.getCapabilities(),
                runtime.client.listGoals(),
                runtime.client.listTasks(),
                runtime.client.listArtifacts(),
                runtime.client.listApprovals()
            ]);
            const capabilitySnapshot = (0, capabilityGate_1.buildCapabilitySnapshot)(capabilities);
            const workflowAvailability = workflowDefaultState();
            for (const commandId of capabilityGate_1.WORKFLOW_COMMANDS) {
                workflowAvailability[commandId] = (0, capabilityGate_1.evaluateWorkflowCommand)(capabilitySnapshot, commandId).allowed;
            }
            const approvalApprove = (0, capabilityGate_1.evaluateCapabilityAction)(capabilitySnapshot, {
                actionId: COMMANDS.approveApproval,
                requiredCapability: "approvals",
                actionAliases: ["approvals.approve", "approve_approval"]
            }).allowed;
            const approvalReject = (0, capabilityGate_1.evaluateCapabilityAction)(capabilitySnapshot, {
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
            const goalCount = readItems(goals.data).length;
            const taskCount = readItems(tasks.data).length;
            const artifactCount = readItems(artifacts.data).length;
            const approvalCount = readItems(approvals.data).length;
            const details = [
                `refresh_source=${source}`,
                `health_status=${health.statusCode ?? "none"}`,
                `capabilities_status=${capabilities.statusCode ?? "none"}`
            ];
            setRuntimeUi(health.state, capabilities.state, runtime.settings.baseUrl, runtime.settings.profileId, details);
            setRuntimeOverview(runtime, health.state, capabilities.state, { goals: goalCount, tasks: taskCount, artifacts: artifactCount, approvals: approvalCount }, details);
            return runtime;
        }
        catch (error) {
            const safeError = (0, redaction_1.sanitizeErrorMessage)(error);
            output.appendLine(`[sidebar] refresh failed=${safeError}`);
            capabilityState = defaultCapabilityState();
            await applyWorkflowContexts(capabilityState.workflowAvailability);
            await applyApprovalActionContexts(false, false);
            setRuntimeUi("backend_unreachable", "unknown", runtime.settings.baseUrl, runtime.settings.profileId, [safeError]);
            resetSidebarViews(`backend_unreachable:${safeError}`);
            return runtime;
        }
    }
    async function openResultLink(links) {
        if (links.length === 0) {
            return;
        }
        const picked = await vscode.window.showQuickPick(links.map((link) => ({ label: link.label, description: link.url, link })), {
            title: "Open Ananta result",
            placeHolder: "Select result link to open in browser"
        });
        if (!picked) {
            return;
        }
        await vscode.env.openExternal(vscode.Uri.parse(picked.link.url));
    }
    async function confirmContextPreview(workflow, preview) {
        if (preview.blockedReasons.length > 0) {
            const detail = buildPreviewDetail(preview);
            await vscode.window.showErrorMessage(`${workflow.title} blocked due to high-risk secret detection (${preview.blockedReasons.join(", ")}).`, { modal: true, detail });
            return false;
        }
        const detail = buildPreviewDetail(preview);
        if (preview.warnings.length > 0) {
            const choice = await vscode.window.showWarningMessage(`${workflow.title} context has warnings. Submit redacted payload?`, { modal: true, detail }, "Submit", "Cancel");
            return choice === "Submit";
        }
        const choice = await vscode.window.showInformationMessage(`${workflow.title}: review context preview before sending.`, { modal: true, detail }, "Submit", "Cancel");
        return choice === "Submit";
    }
    async function promptGoalText(workflow) {
        if (!workflow.requiresGoalInput) {
            return workflow.defaultGoalText;
        }
        return ((await vscode.window.showInputBox({
            title: `Ananta ${workflow.title}`,
            prompt: "Goal text",
            value: workflow.defaultGoalText,
            ignoreFocusOut: true,
            validateInput(value) {
                const normalized = String(value || "").trim();
                if (normalized.length === 0) {
                    return "Goal text must not be empty.";
                }
                if (normalized.length > 1200) {
                    return "Goal text must be <= 1200 characters.";
                }
                return null;
            }
        })) ?? null);
    }
    async function chooseQuickGoalMode() {
        const picked = await vscode.window.showQuickPick(QUICK_GOAL_MODES.map((entry) => ({
            label: entry.label,
            description: entry.description,
            workflow: entry.workflow
        })), {
            title: "Ananta Goal Mode",
            placeHolder: "Select goal mode"
        });
        return picked?.workflow ?? null;
    }
    async function openGoalOrTaskDetailInternal(runtime, ref) {
        if (ref.kind === "goal") {
            const goal = await runtime.client.getGoal(ref.id);
            if (!goal.ok) {
                const fallback = `${runtime.settings.baseUrl.replace(/\/+$/, "")}/goals/${encodeURIComponent(ref.id)}`;
                await vscode.env.openExternal(vscode.Uri.parse(fallback));
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
    async function openArtifactDetailInternal(runtime, ref) {
        const artifact = await runtime.client.getArtifact(ref.id);
        if (!artifact.ok) {
            const fallback = `${runtime.settings.baseUrl.replace(/\/+$/, "")}/artifacts/${encodeURIComponent(ref.id)}`;
            await vscode.env.openExternal(vscode.Uri.parse(fallback));
            void vscode.window.showWarningMessage(`Artifact detail degraded (${artifact.state}); browser fallback opened.`);
            return;
        }
        const payload = asRecord(artifact.data);
        if (!payload) {
            openJsonPanel(`Ananta Artifact ${ref.id}`, {
                schema: "vscode_artifact_detail_v1",
                artifact_id: ref.id,
                unsupported_payload: artifact.data,
                fallback_url: `${runtime.settings.baseUrl.replace(/\/+$/, "")}/artifacts/${encodeURIComponent(ref.id)}`
            });
            return;
        }
        if (!isTextArtifact(payload)) {
            const fallback = `${runtime.settings.baseUrl.replace(/\/+$/, "")}/artifacts/${encodeURIComponent(ref.id)}`;
            await vscode.env.openExternal(vscode.Uri.parse(fallback));
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
    async function openApprovalDetailInternal(runtime, ref) {
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
    async function renderWorkflowResult(runtime, workflowTitle, data) {
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
            browser_links: (0, resultLinks_1.buildResultLinks)(runtime.settings.baseUrl, data)
        });
    }
    async function executeWorkflowCommand(commandId) {
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
        const packaged = (0, contextCapture_1.packageEditorContext)(rawContext);
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
        const metadata = {
            operationPreset: workflow.operationPreset,
            commandId: workflow.id,
            profileId: runtime.settings.profileId,
            runtimeTarget: runtime.settings.runtimeTarget,
            mode: workflow.operationPreset
        };
        const response = (await workflow.run(runtime.client, packaged.payload, goalText, metadata));
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
        const links = (0, resultLinks_1.buildResultLinks)(runtime.settings.baseUrl, response.data);
        output.appendLine(`[workflow] success command=${workflow.id} task_id=${taskId || "none"}`);
        const action = await vscode.window.showInformationMessage(message, "Open Detail", "Open Browser", "Open Status");
        if (action === "Open Detail") {
            await renderWorkflowResult(runtime, workflow.title, response.data);
        }
        else if (action === "Open Browser") {
            await openResultLink(links);
        }
        else if (action === "Open Status") {
            await vscode.commands.executeCommand(COMMANDS.openStatusView);
        }
        await refreshSidebarData("workflow_success");
    }
    async function runApprovalAction(ref, action) {
        const runtime = await refreshSidebarData(`approval_${action}_preflight`);
        if (!runtime) {
            return;
        }
        const allowed = action === "approve" ? capabilityState.approvalActions.approve : capabilityState.approvalActions.reject;
        if (!allowed) {
            void vscode.window.showWarningMessage(`Approval action denied: ${action} is not permitted by backend capabilities.`);
            return;
        }
        const choice = await vscode.window.showWarningMessage(`${action === "approve" ? "Approve" : "Reject"} approval ${ref.id}?`, { modal: true, detail: "This calls backend approval APIs only; no local state mutation is performed." }, action === "approve" ? "Approve" : "Reject", "Cancel");
        if (!choice || choice === "Cancel") {
            return;
        }
        const comment = await vscode.window.showInputBox({
            title: `${action === "approve" ? "Approve" : "Reject"} approval ${ref.id}`,
            prompt: "Optional comment",
            ignoreFocusOut: true
        });
        const response = action === "approve"
            ? await runtime.client.approveApproval(ref.id, comment ?? "")
            : await runtime.client.rejectApproval(ref.id, comment ?? "");
        if (!response.ok) {
            void vscode.window.showWarningMessage(`Approval action degraded (${response.state}). Stale/denied/already-handled state may apply.`);
        }
        else {
            void vscode.window.showInformationMessage(`Approval ${ref.id} ${action === "approve" ? "approved" : "rejected"}.`);
        }
        await refreshSidebarData(`approval_${action}_complete`);
    }
    await applyWorkflowContexts(workflowDefaultState());
    await applyApprovalActionContexts(false, false);
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.configureProfile, async () => {
        await vscode.commands.executeCommand("workbench.action.openSettings", "ananta.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.openStatusView, async () => {
        await vscode.commands.executeCommand("workbench.view.extension.ananta");
        await vscode.commands.executeCommand("ananta.statusView.focus");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.refreshSidebarData, async () => {
        await refreshSidebarData("manual_refresh");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.setGoalTaskStatusFilter, async () => {
        const statuses = goalsTasksView.availableStatuses();
        const picked = await vscode.window.showQuickPick([{ label: "all", description: "Show all statuses" }, ...statuses.map((status) => ({ label: status }))], {
            title: "Filter Goals/Tasks by status",
            placeHolder: "Select status filter"
        });
        if (!picked) {
            return;
        }
        goalsTasksView.setFilter(picked.label);
        runtimeView.refresh();
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.storeToken, async () => {
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
        const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
        await secretStore.storeToken(value, key);
        output.appendLine(`[auth] token stored with key=${key}`);
        void vscode.window.showInformationMessage("Ananta token stored in SecretStorage.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.clearToken, async () => {
        const config = vscode.workspace.getConfiguration("ananta");
        const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
        const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
        await secretStore.clearToken(key);
        output.appendLine(`[auth] token cleared with key=${key}`);
        void vscode.window.showInformationMessage("Ananta token removed from SecretStorage.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.checkHealth, async () => {
        const runtime = await refreshSidebarData("check_health");
        if (!runtime) {
            return;
        }
        const healthy = capabilityState.workflowAvailability[COMMANDS.submitGoal];
        if (healthy) {
            void vscode.window.showInformationMessage("Ananta runtime health/capabilities were refreshed.");
        }
        else {
            void vscode.window.showWarningMessage("Ananta runtime refreshed with degraded capability state.");
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.openGoalOrTaskDetail, async (ref) => {
        if (!ref || !ref.id) {
            void vscode.window.showWarningMessage("No goal/task selected.");
            return;
        }
        const runtime = await refreshSidebarData("open_goal_task_detail");
        if (!runtime) {
            return;
        }
        await openGoalOrTaskDetailInternal(runtime, ref);
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.openArtifactDetail, async (ref) => {
        if (!ref || !ref.id) {
            void vscode.window.showWarningMessage("No artifact selected.");
            return;
        }
        const runtime = await refreshSidebarData("open_artifact_detail");
        if (!runtime) {
            return;
        }
        await openArtifactDetailInternal(runtime, ref);
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.openApprovalDetail, async (ref) => {
        if (!ref || !ref.id) {
            void vscode.window.showWarningMessage("No approval selected.");
            return;
        }
        const runtime = await refreshSidebarData("open_approval_detail");
        if (!runtime) {
            return;
        }
        await openApprovalDetailInternal(runtime, ref);
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.approveApproval, async (ref) => {
        if (!ref || !ref.id) {
            void vscode.window.showWarningMessage("No approval selected for approve action.");
            return;
        }
        await runApprovalAction(ref, "approve");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.rejectApproval, async (ref) => {
        if (!ref || !ref.id) {
            void vscode.window.showWarningMessage("No approval selected for reject action.");
            return;
        }
        await runApprovalAction(ref, "reject");
    }));
    for (const workflowCommand of capabilityGate_1.WORKFLOW_COMMANDS) {
        context.subscriptions.push(vscode.commands.registerCommand(workflowCommand, async () => {
            await executeWorkflowCommand(workflowCommand);
        }));
    }
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(async (event) => {
        if (!event.affectsConfiguration("ananta")) {
            return;
        }
        capabilityState = defaultCapabilityState();
        await applyWorkflowContexts(capabilityState.workflowAvailability);
        await applyApprovalActionContexts(false, false);
        goalsTasksView.setFilter("all");
        await refreshSidebarData("configuration_changed");
    }));
    await refreshSidebarData("activate");
}
function deactivate() {
    // no-op
}
//# sourceMappingURL=extension.js.map