import { beforeEach, describe, expect, it, vi } from "vitest";
import { activate } from "../src/extension";
import * as vscode from "vscode";

interface FetchCall {
  method: string;
  path: string;
  body: string;
}

function installFetchStub(capabilities: string[]): FetchCall[] {
  const calls: FetchCall[] = [];
  globalThis.fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
    const method = String(init?.method || "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : "";
    const parsedUrl = new URL(String(url));
    const path = `${parsedUrl.pathname}${parsedUrl.search}`;
    calls.push({ method, path, body });

    const payloadByPath: Record<string, unknown> = {
      "/health": { state: "ready" },
      "/capabilities": { capabilities },
      "/goals": { items: [{ id: "goal-1", state: "open", title: "Goal one" }] },
      "/tasks": { items: [{ id: "task-1", status: "queued", title: "Task one" }] },
      "/artifacts": { items: [{ id: "artifact-1", type: "report", status: "ready", title: "Artifact one" }] },
      "/approvals": { items: [{ id: "approval-1", state: "pending", summary: "Review required" }] },
      "/api/system/audit-logs?limit=30&offset=0": { items: [{ id: "audit-1", summary: "ok", state: "recorded" }] },
      "/repairs": { items: [{ session_id: "repair-1", diagnosis: "none", dry_run_status: "n/a" }] },
      "/dashboard/read-model?benchmark_task_kind=analysis&include_task_snapshot=1": { active_profile_id: "default" },
      "/assistant/read-model": { active_mode: "operator" },
      "/providers": { items: [{ id: "provider-1", provider: "ollama" }] },
      "/providers/catalog": { providers: ["ollama"] },
      "/llm/benchmarks?task_kind=analysis&top_n=3": { items: [{ model: "llama3", score: 0.9 }] },
      "/config": { governance_mode: "strict" }
    };

    if (method === "POST" && path === "/goals") {
      return {
        status: 200,
        async text(): Promise<string> {
          return JSON.stringify({ goal_id: "goal-2", task_id: "task-2" });
        }
      };
    }

    const payload = payloadByPath[path];
    if (payload === undefined) {
      return {
        status: 404,
        async text(): Promise<string> {
          return JSON.stringify({ error: "not_found" });
        }
      };
    }
    return {
      status: 200,
      async text(): Promise<string> {
        return JSON.stringify(payload);
      }
    };
  }) as unknown as typeof fetch;
  return calls;
}

describe("extension smoke", () => {
  beforeEach(() => {
    vscode.__resetMock();
    vscode.__setConfig({
      baseUrl: "http://localhost:8080",
      profileId: "default",
      runtimeTarget: "local",
      "auth.mode": "session_token",
      "auth.secretStorageKey": "ananta.auth.token",
      timeoutMs: 8000
    });
    vscode.__setSecret("ananta.auth.token", "fixture-token");
  });

  it("activates, registers commands, and executes mocked health + submit-goal flow", async () => {
    const calls = installFetchStub(["goals", "review", "patch", "projects", "approvals"]);
    const context = vscode.__createExtensionContext();
    vscode.__queueQuickPick(0);
    vscode.__queueInputBox("Ship smoke-tested goal");
    vscode.__queueInformationChoice("Submit");

    await activate(context as never);

    const registeredCommands = vscode.__getRegisteredCommands();
    expect(registeredCommands).toContain("ananta.checkHealth");
    expect(registeredCommands).toContain("ananta.submitGoal");
    expect(registeredCommands).toContain("ananta.openGoalOrTaskDetail");
    expect(vscode.__getContextValue("ananta.capability.submitGoal")).toBe(true);

    await vscode.commands.executeCommand("ananta.submitGoal");
    await vscode.commands.executeCommand("ananta.checkHealth");

    expect(calls.some((call) => call.method === "GET" && call.path === "/health")).toBe(true);
    expect(calls.some((call) => call.method === "POST" && call.path === "/goals")).toBe(true);
    expect(calls.some((call) => call.method === "POST" && call.path.startsWith("/repairs"))).toBe(false);
    expect(vscode.__getApplyEditCount()).toBe(0);
  });

  it("denies submit goal when capability gate is not granted", async () => {
    const calls = installFetchStub(["review"]);
    const context = vscode.__createExtensionContext();
    vscode.__queueQuickPick(0);

    await activate(context as never);
    await vscode.commands.executeCommand("ananta.submitGoal");

    expect(calls.some((call) => call.method === "POST" && call.path === "/goals")).toBe(false);
    expect(vscode.__getWarningMessages().some((message) => message.includes("denied"))).toBe(true);
  });
});
