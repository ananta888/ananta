import * as vscode from "vscode";
import {
  COMMANDS, capabilityRef, workflowDefaultState, defaultCapabilityState,
  applyWorkflowContexts, applyApprovalActionContexts, WebFallbackArgs,
  openWebFallbackInternal, launchTuiCmd, storeTokenCmd, clearTokenCmd, checkHealthCmd
} from "./commands/helpers";
import { executeWorkflowCommand, openGoalOrTaskDetailInternal } from "./commands/goal-commands";
import {
  openArtifactDetailInternal, openApprovalDetailInternal,
  openAuditDetailInternal, openRepairDetailInternal, runApprovalAction
} from "./commands/task-commands";
import { createSidebarManager, setGoalTaskStatusFilterCmd } from "./commands/sidebar-manager";
import {
  GoalTaskRef, ArtifactRef, ApprovalRef, AuditRef, RepairRef,
  GoalsTasksTreeProvider, ArtifactsTreeProvider, ApprovalQueueTreeProvider,
  AuditTreeProvider, RepairTreeProvider, RuntimeOverviewTreeProvider
} from "./views/sidebarProviders";
import { AnantaStatusTreeProvider } from "./views/statusTreeProvider";
import { WORKFLOW_COMMANDS } from "./runtime/capabilityGate";

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

  context.subscriptions.push(output, diagnostics, statusBar);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.statusView", statusView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.goalsTasksView", goalsTasksView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.artifactsView", artifactsView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.approvalsView", approvalsView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.auditView", auditView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.repairView", repairView));
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.runtimeView", runtimeView));

  const mgr = createSidebarManager({ context, output, statusView, goalsTasksView, artifactsView, approvalsView, auditView, repairView, runtimeView, diagnostics, diagnosticsUri, statusBar });
  const { refreshSidebarData, setRuntimeUi } = mgr;

  capabilityRef.current = defaultCapabilityState();
  await applyWorkflowContexts(capabilityRef.current.workflowAvailability);
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
      if (!runtime) return;
      await openWebFallbackInternal(runtime, args);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.launchTui, async () => {
      const runtime = await refreshSidebarData("launch_tui");
      if (!runtime) return;
      await launchTuiCmd(runtime);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.setGoalTaskStatusFilter, async () => {
      await setGoalTaskStatusFilterCmd(goalsTasksView, runtimeView);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.storeToken, async () => { await storeTokenCmd(context, output); })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.clearToken, async () => { await clearTokenCmd(context, output); })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.checkHealth, async () => { await checkHealthCmd(refreshSidebarData); })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openGoalOrTaskDetail, async (ref?: GoalTaskRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No goal/task selected."); return; }
      const runtime = await refreshSidebarData("open_goal_task_detail");
      if (!runtime) return;
      await openGoalOrTaskDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openArtifactDetail, async (ref?: ArtifactRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No artifact selected."); return; }
      const runtime = await refreshSidebarData("open_artifact_detail");
      if (!runtime) return;
      await openArtifactDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openApprovalDetail, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No approval selected."); return; }
      const runtime = await refreshSidebarData("open_approval_detail");
      if (!runtime) return;
      await openApprovalDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openAuditDetail, async (ref?: AuditRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No audit entry selected."); return; }
      const runtime = await refreshSidebarData("open_audit_detail");
      if (!runtime) return;
      await openAuditDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.openRepairDetail, async (ref?: RepairRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No repair session selected."); return; }
      const runtime = await refreshSidebarData("open_repair_detail");
      if (!runtime) return;
      await openRepairDetailInternal(runtime, ref);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.approveApproval, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No approval selected for approve action."); return; }
      await runApprovalAction(ref, "approve", refreshSidebarData);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.rejectApproval, async (ref?: ApprovalRef) => {
      if (!ref || !ref.id) { void vscode.window.showWarningMessage("No approval selected for reject action."); return; }
      await runApprovalAction(ref, "reject", refreshSidebarData);
    })
  );

  for (const workflowCommand of WORKFLOW_COMMANDS) {
    context.subscriptions.push(
      vscode.commands.registerCommand(workflowCommand, async () => {
        await executeWorkflowCommand(workflowCommand, refreshSidebarData, setRuntimeUi);
      })
    );
  }

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (!event.affectsConfiguration("ananta")) return;
      capabilityRef.current = defaultCapabilityState();
      await applyWorkflowContexts(capabilityRef.current.workflowAvailability);
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
