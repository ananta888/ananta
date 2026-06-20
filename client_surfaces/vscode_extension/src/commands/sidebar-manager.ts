import * as vscode from "vscode";
import { AnantaBackendClient } from "../runtime/backendClient";
import { AnantaSecretStore } from "../runtime/secretStore";
import { resolveRuntimeSettings } from "../runtime/settings";
import { buildCapabilitySnapshot, evaluateWorkflowCommand, evaluateCapabilityAction, WorkflowCommandId } from "../runtime/capabilityGate";
import { sanitizeErrorMessage } from "../runtime/redaction";
import { AnantaStatusTreeProvider } from "../views/statusTreeProvider";
import {
  GoalsTasksTreeProvider,
  ArtifactsTreeProvider,
  ApprovalQueueTreeProvider,
  AuditTreeProvider,
  RepairTreeProvider,
  RuntimeOverviewTreeProvider
} from "../views/sidebarProviders";
import {
  COMMANDS,
  RuntimeClientContext,
  capabilityRef,
  workflowDefaultState,
  defaultCapabilityState,
  readString,
  readItems,
  firstRecord,
  redactSensitiveValue,
  extractProviderSummary,
  extractModelSummary,
  extractGovernanceSummary,
  statusBarText,
  diagnosticSeverity,
  applyWorkflowContexts,
  applyApprovalActionContexts
} from "./helpers";

export interface SidebarManagerDeps {
  context: vscode.ExtensionContext;
  output: vscode.OutputChannel;
  statusView: AnantaStatusTreeProvider;
  goalsTasksView: GoalsTasksTreeProvider;
  artifactsView: ArtifactsTreeProvider;
  approvalsView: ApprovalQueueTreeProvider;
  auditView: AuditTreeProvider;
  repairView: RepairTreeProvider;
  runtimeView: RuntimeOverviewTreeProvider;
  diagnostics: vscode.DiagnosticCollection;
  diagnosticsUri: vscode.Uri;
  statusBar: vscode.StatusBarItem;
}

export interface SidebarManager {
  refreshSidebarData: (source: string) => Promise<RuntimeClientContext | null>;
  setRuntimeUi: (connectionState: string, capabilitiesState: string, endpoint: string, profileId: string, details: string[]) => void;
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
    output.appendLine(`[runtime] ${message}`);
    void vscode.window.showWarningMessage(message);
    return null;
  }
  return {
    client: new AnantaBackendClient(resolved.settings),
    settings: resolved.settings
  };
}

export async function setGoalTaskStatusFilterCmd(
  goalsTasksView: GoalsTasksTreeProvider,
  runtimeView: RuntimeOverviewTreeProvider
): Promise<void> {
  const statuses = goalsTasksView.availableStatuses();
  const picked = await vscode.window.showQuickPick(
    [{ label: "all", description: "Show all statuses" }, ...statuses.map((status) => ({ label: status }))],
    { title: "Filter Goals/Tasks by status", placeHolder: "Select status filter" }
  );
  if (!picked) return;
  goalsTasksView.setFilter(picked.label);
  runtimeView.refresh();
}

export function createSidebarManager(deps: SidebarManagerDeps): SidebarManager {
  const { context, output, statusView, goalsTasksView, artifactsView, approvalsView, auditView, repairView, runtimeView, diagnostics, diagnosticsUri, statusBar } = deps;

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

  const refreshSidebarData = async (source: string): Promise<RuntimeClientContext | null> => {
    const runtime = await buildRuntimeClient(context, statusView, output);
    if (!runtime) {
      capabilityRef.current = defaultCapabilityState();
      await applyWorkflowContexts(capabilityRef.current.workflowAvailability);
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
      for (const commandId of Object.keys(workflowAvailability) as WorkflowCommandId[]) {
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

      capabilityRef.current = {
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
      capabilityRef.current = defaultCapabilityState();
      await applyWorkflowContexts(capabilityRef.current.workflowAvailability);
      await applyApprovalActionContexts(false, false);
      setRuntimeUi("backend_unreachable", "unknown", runtime.settings.baseUrl, runtime.settings.profileId, [safeError]);
      resetSidebarViews(`backend_unreachable:${safeError}`);
      return runtime;
    }
  };

  return { refreshSidebarData, setRuntimeUi };
}
