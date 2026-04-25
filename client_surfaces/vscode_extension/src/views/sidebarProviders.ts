import * as vscode from "vscode";

export type GoalTaskKind = "goal" | "task";

export interface GoalTaskRef {
  kind: GoalTaskKind;
  id: string;
}

export interface ArtifactRef {
  id: string;
}

export interface ApprovalRef {
  id: string;
  state: string;
}

export interface AuditRef {
  id: string;
  relatedGoalId?: string;
  relatedTaskId?: string;
  relatedArtifactId?: string;
  traceId?: string;
}

export interface RepairRef {
  id: string;
}

export interface RuntimeOverviewSnapshot {
  connectionState: string;
  capabilitiesState: string;
  endpoint: string;
  profileId: string;
  goalCount: number;
  taskCount: number;
  artifactCount: number;
  approvalCount: number;
  auditCount: number;
  repairCount: number;
  filterStatus: string;
  details: string[];
}

class MessageItem extends vscode.TreeItem {
  public constructor(label: string, description?: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  const items: Array<Record<string, unknown>> = [];
  for (const candidate of value) {
    const parsed = asRecord(candidate);
    if (parsed) {
      items.push(parsed);
    }
  }
  return items;
}

function readItems(payload: unknown): Array<Record<string, unknown>> {
  const direct = asRecordArray(payload);
  if (direct.length > 0) {
    return direct;
  }
  const record = asRecord(payload);
  if (!record) {
    return [];
  }
  return asRecordArray(record.items);
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

function readStatus(record: Record<string, unknown>): string {
  return readString(record, "state", "status") || "unknown";
}

class GoalTaskItem extends vscode.TreeItem {
  public constructor(
    public readonly ref: GoalTaskRef,
    label: string,
    description: string,
    tooltip: string
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip;
    this.contextValue = "ananta.goalTask.item";
    this.command = {
      command: "ananta.openGoalOrTaskDetail",
      title: "Open detail",
      arguments: [this.ref]
    };
    this.iconPath = new vscode.ThemeIcon(ref.kind === "goal" ? "target" : "checklist");
  }
}

export class GoalsTasksTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private goals: Array<Record<string, unknown>> = [];
  private tasks: Array<Record<string, unknown>> = [];
  private degradedReason = "";
  private statusFilter = "all";

  public setData(goalsPayload: unknown, tasksPayload: unknown, degradedReason = ""): void {
    this.goals = readItems(goalsPayload);
    this.tasks = readItems(tasksPayload);
    this.degradedReason = degradedReason;
    this.onDidChangeEmitter.fire();
  }

  public setFilter(status: string): void {
    this.statusFilter = String(status || "all").trim() || "all";
    this.onDidChangeEmitter.fire();
  }

  public getFilter(): string {
    return this.statusFilter;
  }

  public availableStatuses(): string[] {
    const statuses = new Set<string>();
    for (const item of [...this.goals, ...this.tasks]) {
      statuses.add(readStatus(item));
    }
    return Array.from(statuses).sort();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    if (this.degradedReason) {
      return [new MessageItem("Goals/Tasks unavailable", this.degradedReason)];
    }

    const items: vscode.TreeItem[] = [];
    const filter = this.statusFilter.toLowerCase();
    for (const goal of this.goals) {
      const id = readString(goal, "id", "goal_id");
      const status = readStatus(goal);
      if (!id) {
        continue;
      }
      if (filter !== "all" && status.toLowerCase() !== filter) {
        continue;
      }
      const title = readString(goal, "title", "name") || id;
      const mode = readString(goal, "mode");
      const profile = readString(goal, "profile_id");
      const description = [`status=${status}`, mode ? `mode=${mode}` : "", profile ? `profile=${profile}` : ""]
        .filter(Boolean)
        .join(" ");
      items.push(new GoalTaskItem({ kind: "goal", id }, `[Goal] ${title}`, description, JSON.stringify(goal, null, 2)));
    }

    for (const task of this.tasks) {
      const id = readString(task, "id", "task_id");
      const status = readStatus(task);
      if (!id) {
        continue;
      }
      if (filter !== "all" && status.toLowerCase() !== filter) {
        continue;
      }
      const title = readString(task, "title", "summary", "name") || id;
      const mode = readString(task, "mode");
      const team = readString(task, "team_id");
      const profile = readString(task, "profile_id");
      const description = [status, mode ? `mode=${mode}` : "", team ? `team=${team}` : "", profile ? `profile=${profile}` : ""]
        .filter(Boolean)
        .join(" ");
      items.push(new GoalTaskItem({ kind: "task", id }, `[Task] ${title}`, description, JSON.stringify(task, null, 2)));
    }

    if (items.length === 0) {
      return [new MessageItem("No goals/tasks", this.statusFilter === "all" ? "No entries returned." : `Filter=${this.statusFilter}`)];
    }
    return items;
  }
}

class ArtifactItem extends vscode.TreeItem {
  public constructor(
    public readonly ref: ArtifactRef,
    label: string,
    description: string,
    tooltip: string,
    binaryLike: boolean
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip;
    this.contextValue = binaryLike ? "ananta.artifact.binary" : "ananta.artifact.text";
    this.command = {
      command: "ananta.openArtifactDetail",
      title: "Open artifact",
      arguments: [this.ref]
    };
    this.iconPath = new vscode.ThemeIcon(binaryLike ? "file-binary" : "file-code");
  }
}

function isBinaryLikeType(value: string): boolean {
  const lowered = value.toLowerCase();
  return (
    lowered.includes("binary") ||
    lowered.includes("image") ||
    lowered.includes("pdf") ||
    lowered.includes("zip") ||
    lowered.includes("audio") ||
    lowered.includes("video")
  );
}

export class ArtifactsTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private artifacts: Array<Record<string, unknown>> = [];
  private degradedReason = "";

  public setData(payload: unknown, degradedReason = ""): void {
    this.artifacts = readItems(payload);
    this.degradedReason = degradedReason;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    if (this.degradedReason) {
      return [new MessageItem("Artifacts unavailable", this.degradedReason)];
    }
    if (this.artifacts.length === 0) {
      return [new MessageItem("No artifacts", "No artifact items returned.")];
    }
    const items: vscode.TreeItem[] = [];
    for (const artifact of this.artifacts) {
      const id = readString(artifact, "id", "artifact_id");
      if (!id) {
        continue;
      }
      const title = readString(artifact, "title", "name") || id;
      const type = readString(artifact, "type", "artifact_type", "mime_type") || "unknown";
      const status = readStatus(artifact);
      const binaryLike = isBinaryLikeType(type);
      items.push(new ArtifactItem({ id }, title, `${type} ${status}`, JSON.stringify(artifact, null, 2), binaryLike));
    }
    return items.length > 0 ? items : [new MessageItem("No artifacts", "No valid artifact entries returned.")];
  }
}

class ApprovalItem extends vscode.TreeItem {
  public constructor(public readonly ref: ApprovalRef, label: string, description: string, tooltip: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip;
    this.contextValue = ref.state.toLowerCase() === "pending" ? "ananta.approval.pending" : "ananta.approval.item";
    this.command = {
      command: "ananta.openApprovalDetail",
      title: "Open approval",
      arguments: [this.ref]
    };
    this.iconPath = new vscode.ThemeIcon("pass-filled");
  }
}

export class ApprovalQueueTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private approvals: Array<Record<string, unknown>> = [];
  private degradedReason = "";

  public setData(payload: unknown, degradedReason = ""): void {
    this.approvals = readItems(payload);
    this.degradedReason = degradedReason;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    if (this.degradedReason) {
      return [new MessageItem("Approvals unavailable", this.degradedReason)];
    }
    if (this.approvals.length === 0) {
      return [new MessageItem("No approvals", "No pending review/approval entries.")];
    }
    const items: vscode.TreeItem[] = [];
    for (const approval of this.approvals) {
      const id = readString(approval, "id", "approval_id");
      if (!id) {
        continue;
      }
      const state = readStatus(approval);
      const scope = readString(approval, "scope", "approval_scope");
      const summary = readString(approval, "summary", "context_summary");
      const description = [`state=${state}`, scope ? `scope=${scope}` : ""].filter(Boolean).join(" ");
      const tooltip = JSON.stringify(approval, null, 2);
      items.push(new ApprovalItem({ id, state }, summary ? `${id}: ${summary}` : id, description, tooltip));
    }
    return items.length > 0 ? items : [new MessageItem("No approvals", "No valid approval entries returned.")];
  }
}

export class RuntimeOverviewTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private snapshot: RuntimeOverviewSnapshot = {
    connectionState: "idle",
    capabilitiesState: "unknown",
    endpoint: "-",
    profileId: "-",
    goalCount: 0,
    taskCount: 0,
    artifactCount: 0,
    approvalCount: 0,
    auditCount: 0,
    repairCount: 0,
    filterStatus: "all",
    details: []
  };

  public setSnapshot(snapshot: RuntimeOverviewSnapshot): void {
    this.snapshot = snapshot;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    return [
      new MessageItem("Connection", this.snapshot.connectionState),
      new MessageItem("Capabilities", this.snapshot.capabilitiesState),
      new MessageItem("Endpoint", this.snapshot.endpoint),
      new MessageItem("Profile", this.snapshot.profileId),
      new MessageItem("Goals", String(this.snapshot.goalCount)),
      new MessageItem("Tasks", String(this.snapshot.taskCount)),
      new MessageItem("Artifacts", String(this.snapshot.artifactCount)),
      new MessageItem("Approvals", String(this.snapshot.approvalCount)),
      new MessageItem("Audit entries", String(this.snapshot.auditCount)),
      new MessageItem("Repair sessions", String(this.snapshot.repairCount)),
      new MessageItem("Task/Goal Filter", this.snapshot.filterStatus),
      ...this.snapshot.details.map((detail) => new MessageItem(detail))
    ];
  }
}

class AuditItem extends vscode.TreeItem {
  public constructor(public readonly ref: AuditRef, label: string, description: string, tooltip: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip;
    this.contextValue = "ananta.audit.item";
    this.command = {
      command: "ananta.openAuditDetail",
      title: "Open audit detail",
      arguments: [this.ref]
    };
    this.iconPath = new vscode.ThemeIcon("history");
  }
}

export class AuditTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private audits: Array<Record<string, unknown>> = [];
  private degradedReason = "";

  public setData(payload: unknown, degradedReason = ""): void {
    this.audits = readItems(payload);
    this.degradedReason = degradedReason;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    if (this.degradedReason) {
      return [new MessageItem("Audit unavailable", this.degradedReason)];
    }
    if (this.audits.length === 0) {
      return [new MessageItem("No audit entries", "No audit entries returned.")];
    }

    const items: vscode.TreeItem[] = [];
    for (const audit of this.audits) {
      const id = readString(audit, "id", "audit_id", "event_id");
      if (!id) {
        continue;
      }
      const state = readStatus(audit);
      const category = readString(audit, "category", "event_type", "kind");
      const summary = readString(audit, "summary", "message", "event", "action") || id;
      const relatedGoalId = readString(audit, "goal_id", "related_goal_id");
      const relatedTaskId = readString(audit, "task_id", "related_task_id");
      const relatedArtifactId = readString(audit, "artifact_id", "related_artifact_id");
      const traceId = readString(audit, "trace_id", "trace", "request_id");
      const description = [
        `state=${state}`,
        category ? `category=${category}` : "",
        relatedTaskId ? `task=${relatedTaskId}` : "",
        relatedArtifactId ? `artifact=${relatedArtifactId}` : ""
      ]
        .filter(Boolean)
        .join(" ");
      items.push(
        new AuditItem(
          { id, relatedGoalId, relatedTaskId, relatedArtifactId, traceId },
          summary,
          description,
          JSON.stringify(audit, null, 2)
        )
      );
    }
    return items.length > 0 ? items : [new MessageItem("No audit entries", "No valid audit entries returned.")];
  }
}

class RepairItem extends vscode.TreeItem {
  public constructor(public readonly ref: RepairRef, label: string, description: string, tooltip: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip;
    this.contextValue = "ananta.repair.item";
    this.command = {
      command: "ananta.openRepairDetail",
      title: "Open repair detail",
      arguments: [this.ref]
    };
    this.iconPath = new vscode.ThemeIcon("wrench");
  }
}

export class RepairTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private repairs: Array<Record<string, unknown>> = [];
  private degradedReason = "";

  public setData(payload: unknown, degradedReason = ""): void {
    this.repairs = readItems(payload);
    this.degradedReason = degradedReason;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    if (this.degradedReason) {
      return [new MessageItem("Repair unavailable", this.degradedReason)];
    }
    if (this.repairs.length === 0) {
      return [new MessageItem("No repair sessions", "No repair sessions returned.")];
    }

    const items: vscode.TreeItem[] = [];
    for (const repair of this.repairs) {
      const id = readString(repair, "session_id", "id", "repair_id");
      if (!id) {
        continue;
      }
      const diagnosis = readString(repair, "diagnosis", "summary", "title") || id;
      const dryRun = readString(repair, "dry_run_status", "dry_run_state");
      const approval = readString(repair, "approval_state", "approval_status");
      const verification = readString(repair, "verification_result", "verification_state");
      const description = [
        dryRun ? `dry-run=${dryRun}` : "",
        approval ? `approval=${approval}` : "",
        verification ? `verify=${verification}` : ""
      ]
        .filter(Boolean)
        .join(" ");
      items.push(new RepairItem({ id }, diagnosis, description, JSON.stringify(repair, null, 2)));
    }
    return items.length > 0 ? items : [new MessageItem("No repair sessions", "No valid repair entries returned.")];
  }
}
