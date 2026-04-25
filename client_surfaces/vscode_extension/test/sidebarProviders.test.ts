import { describe, expect, it } from "vitest";
import {
  ApprovalQueueTreeProvider,
  ArtifactsTreeProvider,
  AuditTreeProvider,
  GoalsTasksTreeProvider,
  RepairTreeProvider,
  RuntimeOverviewTreeProvider
} from "../src/views/sidebarProviders";

function labels(items: unknown[]): string[] {
  return items.map((item) => String((item as { label?: string }).label || ""));
}

describe("sidebar providers", () => {
  it("renders goals/tasks entries and applies status filter", () => {
    const provider = new GoalsTasksTreeProvider();
    provider.setData(
      { items: [{ id: "goal-1", title: "Goal A", state: "open" }] },
      { items: [{ id: "task-1", title: "Task A", status: "queued" }] }
    );

    const allItems = provider.getChildren() as unknown[];
    expect(labels(allItems)).toEqual(["[Goal] Goal A", "[Task] Task A"]);

    provider.setFilter("queued");
    const filteredItems = provider.getChildren() as unknown[];
    expect(labels(filteredItems)).toEqual(["[Task] Task A"]);
  });

  it("renders artifact, approval, audit, and repair items with graceful degraded/empty handling", () => {
    const artifacts = new ArtifactsTreeProvider();
    artifacts.setData({ items: [{ id: "artifact-1", title: "Result", type: "image/png", status: "ready" }] });
    const artifactItems = artifacts.getChildren() as Array<{ contextValue?: string }>;
    expect(artifactItems[0]?.contextValue).toBe("ananta.artifact.binary");

    const approvals = new ApprovalQueueTreeProvider();
    approvals.setData({ items: [{ id: "approval-1", state: "pending", summary: "Needs review" }] });
    const approvalItems = approvals.getChildren() as Array<{ contextValue?: string }>;
    expect(approvalItems[0]?.contextValue).toBe("ananta.approval.pending");

    const audits = new AuditTreeProvider();
    audits.setData({ items: [{ id: "audit-1", summary: "event", state: "recorded", task_id: "task-1" }] });
    expect(labels(audits.getChildren() as unknown[])).toEqual(["event"]);

    const repairs = new RepairTreeProvider();
    repairs.setData({ items: [{ session_id: "repair-1", diagnosis: "missing dependency", dry_run_status: "ready" }] });
    expect(labels(repairs.getChildren() as unknown[])).toEqual(["missing dependency"]);
  });

  it("renders runtime overview counters and details", () => {
    const runtime = new RuntimeOverviewTreeProvider();
    runtime.setSnapshot({
      connectionState: "healthy",
      capabilitiesState: "healthy",
      endpoint: "http://localhost:8080",
      profileId: "default",
      goalCount: 1,
      taskCount: 2,
      artifactCount: 3,
      approvalCount: 4,
      auditCount: 5,
      repairCount: 6,
      filterStatus: "all",
      details: ["refresh_source=test"]
    });
    const items = runtime.getChildren() as unknown[];
    expect(labels(items)).toContain("Connection");
    expect(labels(items)).toContain("Audit entries");
    expect(labels(items)).toContain("Repair sessions");
  });
});
