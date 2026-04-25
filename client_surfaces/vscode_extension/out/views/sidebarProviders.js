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
exports.RuntimeOverviewTreeProvider = exports.ApprovalQueueTreeProvider = exports.ArtifactsTreeProvider = exports.GoalsTasksTreeProvider = void 0;
const vscode = __importStar(require("vscode"));
class MessageItem extends vscode.TreeItem {
    constructor(label, description) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.description = description;
    }
}
function asRecord(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
    }
    return value;
}
function asRecordArray(value) {
    if (!Array.isArray(value)) {
        return [];
    }
    const items = [];
    for (const candidate of value) {
        const parsed = asRecord(candidate);
        if (parsed) {
            items.push(parsed);
        }
    }
    return items;
}
function readItems(payload) {
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
function readString(record, ...keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim().length > 0) {
            return value.trim();
        }
    }
    return "";
}
function readStatus(record) {
    return readString(record, "state", "status") || "unknown";
}
class GoalTaskItem extends vscode.TreeItem {
    ref;
    constructor(ref, label, description, tooltip) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.ref = ref;
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
class GoalsTasksTreeProvider {
    onDidChangeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.onDidChangeEmitter.event;
    goals = [];
    tasks = [];
    degradedReason = "";
    statusFilter = "all";
    setData(goalsPayload, tasksPayload, degradedReason = "") {
        this.goals = readItems(goalsPayload);
        this.tasks = readItems(tasksPayload);
        this.degradedReason = degradedReason;
        this.onDidChangeEmitter.fire();
    }
    setFilter(status) {
        this.statusFilter = String(status || "all").trim() || "all";
        this.onDidChangeEmitter.fire();
    }
    getFilter() {
        return this.statusFilter;
    }
    availableStatuses() {
        const statuses = new Set();
        for (const item of [...this.goals, ...this.tasks]) {
            statuses.add(readStatus(item));
        }
        return Array.from(statuses).sort();
    }
    refresh() {
        this.onDidChangeEmitter.fire();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (this.degradedReason) {
            return [new MessageItem("Goals/Tasks unavailable", this.degradedReason)];
        }
        const items = [];
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
exports.GoalsTasksTreeProvider = GoalsTasksTreeProvider;
class ArtifactItem extends vscode.TreeItem {
    ref;
    constructor(ref, label, description, tooltip, binaryLike) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.ref = ref;
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
function isBinaryLikeType(value) {
    const lowered = value.toLowerCase();
    return (lowered.includes("binary") ||
        lowered.includes("image") ||
        lowered.includes("pdf") ||
        lowered.includes("zip") ||
        lowered.includes("audio") ||
        lowered.includes("video"));
}
class ArtifactsTreeProvider {
    onDidChangeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.onDidChangeEmitter.event;
    artifacts = [];
    degradedReason = "";
    setData(payload, degradedReason = "") {
        this.artifacts = readItems(payload);
        this.degradedReason = degradedReason;
        this.onDidChangeEmitter.fire();
    }
    refresh() {
        this.onDidChangeEmitter.fire();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (this.degradedReason) {
            return [new MessageItem("Artifacts unavailable", this.degradedReason)];
        }
        if (this.artifacts.length === 0) {
            return [new MessageItem("No artifacts", "No artifact items returned.")];
        }
        const items = [];
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
exports.ArtifactsTreeProvider = ArtifactsTreeProvider;
class ApprovalItem extends vscode.TreeItem {
    ref;
    constructor(ref, label, description, tooltip) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.ref = ref;
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
class ApprovalQueueTreeProvider {
    onDidChangeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.onDidChangeEmitter.event;
    approvals = [];
    degradedReason = "";
    setData(payload, degradedReason = "") {
        this.approvals = readItems(payload);
        this.degradedReason = degradedReason;
        this.onDidChangeEmitter.fire();
    }
    refresh() {
        this.onDidChangeEmitter.fire();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (this.degradedReason) {
            return [new MessageItem("Approvals unavailable", this.degradedReason)];
        }
        if (this.approvals.length === 0) {
            return [new MessageItem("No approvals", "No pending review/approval entries.")];
        }
        const items = [];
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
exports.ApprovalQueueTreeProvider = ApprovalQueueTreeProvider;
class RuntimeOverviewTreeProvider {
    onDidChangeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.onDidChangeEmitter.event;
    snapshot = {
        connectionState: "idle",
        capabilitiesState: "unknown",
        endpoint: "-",
        profileId: "-",
        goalCount: 0,
        taskCount: 0,
        artifactCount: 0,
        approvalCount: 0,
        filterStatus: "all",
        details: []
    };
    setSnapshot(snapshot) {
        this.snapshot = snapshot;
        this.onDidChangeEmitter.fire();
    }
    refresh() {
        this.onDidChangeEmitter.fire();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        return [
            new MessageItem("Connection", this.snapshot.connectionState),
            new MessageItem("Capabilities", this.snapshot.capabilitiesState),
            new MessageItem("Endpoint", this.snapshot.endpoint),
            new MessageItem("Profile", this.snapshot.profileId),
            new MessageItem("Goals", String(this.snapshot.goalCount)),
            new MessageItem("Tasks", String(this.snapshot.taskCount)),
            new MessageItem("Artifacts", String(this.snapshot.artifactCount)),
            new MessageItem("Approvals", String(this.snapshot.approvalCount)),
            new MessageItem("Task/Goal Filter", this.snapshot.filterStatus),
            ...this.snapshot.details.map((detail) => new MessageItem(detail))
        ];
    }
}
exports.RuntimeOverviewTreeProvider = RuntimeOverviewTreeProvider;
//# sourceMappingURL=sidebarProviders.js.map