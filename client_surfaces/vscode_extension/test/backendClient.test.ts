import { describe, expect, it } from "vitest";
import {
  AnantaBackendClient,
  HttpTransport,
  HttpTransportRequest,
  HttpTransportResponse,
  WorkflowRequestMetadata
} from "../src/runtime/backendClient";
import { RuntimeSettings } from "../src/runtime/types";

const settings: RuntimeSettings = {
  baseUrl: "http://localhost:8080",
  profileId: "default",
  runtimeTarget: "local",
  authMode: "session_token",
  authToken: "fixture-token",
  timeoutMs: 8000,
  secretStorageKey: "ananta.auth.token"
};

const metadata: WorkflowRequestMetadata = {
  operationPreset: "review",
  commandId: "ananta.reviewFile",
  profileId: "default",
  runtimeTarget: "local",
  mode: "review"
};

class StubTransport implements HttpTransport {
  public constructor(private readonly handler: (request: HttpTransportRequest) => Promise<HttpTransportResponse>) {}

  public request(request: HttpTransportRequest): Promise<HttpTransportResponse> {
    return this.handler(request);
  }
}

describe("AnantaBackendClient", () => {
  it("returns healthy response for valid JSON success", async () => {
    const transport = new StubTransport(async (request) => {
      expect(request.url).toBe("http://localhost:8080/health");
      expect(request.headers.Authorization).toBe("Bearer fixture-token");
      return { status: 200, body: JSON.stringify({ state: "ready" }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.getHealth();
    expect(response.ok).toBe(true);
    expect(response.state).toBe("healthy");
    expect(response.data?.state).toBe("ready");
  });

  it("maps malformed JSON to degraded response", async () => {
    const transport = new StubTransport(async () => ({ status: 200, body: "{not-json" }));
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.getCapabilities();
    expect(response.ok).toBe(false);
    expect(response.state).toBe("malformed_response");
    expect(response.retriable).toBe(false);
  });

  it("maps unauthorized to auth_failed", async () => {
    const transport = new StubTransport(async () => ({ status: 401, body: JSON.stringify({ error: "auth_failed" }) }));
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.listGoals();
    expect(response.ok).toBe(false);
    expect(response.state).toBe("auth_failed");
  });

  it("maps thrown transport failures to backend_unreachable", async () => {
    const transport = new StubTransport(async () => {
      throw new Error("socket timeout");
    });
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.listTasks();
    expect(response.ok).toBe(false);
    expect(response.state).toBe("backend_unreachable");
    expect(response.retriable).toBe(true);
  });

  it("sends goal workflow payload with explicit metadata", async () => {
    const transport = new StubTransport(async (request) => {
      expect(request.url).toBe("http://localhost:8080/goals");
      const body = JSON.parse(request.body ?? "{}") as Record<string, unknown>;
      expect(body.goal_text).toBe("Ship the feature");
      expect(body.context).toMatchObject({ schema: "client_bounded_context_payload_v1" });
      expect(body.operation_preset).toBe("review");
      expect(body.command_id).toBe("ananta.reviewFile");
      expect(body.profile_id).toBe("default");
      expect(body.runtime_target).toBe("local");
      expect(body.mode).toBe("review");
      return { status: 200, body: JSON.stringify({ task_id: "task-1" }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.submitGoal(
      "Ship the feature",
      { schema: "client_bounded_context_payload_v1", selection_text: "print('x')" },
      metadata
    );
    expect(response.ok).toBe(true);
    expect(response.data?.task_id).toBe("task-1");
  });

  it("sends analyze payload with contextual goal text", async () => {
    const transport = new StubTransport(async (request) => {
      expect(request.url).toBe("http://localhost:8080/tasks/analyze");
      const body = JSON.parse(request.body ?? "{}") as Record<string, unknown>;
      expect(body.goal_text).toBe("Analyze this selection");
      expect(body.context).toMatchObject({ schema: "client_bounded_context_payload_v1" });
      return { status: 200, body: JSON.stringify({ task_id: "task-analyze-1" }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    const response = await client.analyzeContext(
      { schema: "client_bounded_context_payload_v1", selection_text: "print('x')" },
      metadata,
      "Analyze this selection"
    );
    expect(response.ok).toBe(true);
  });

  it("requests task detail and logs with encoded task id", async () => {
    const calls: string[] = [];
    const transport = new StubTransport(async (request) => {
      calls.push(request.url);
      return { status: 200, body: JSON.stringify({ ok: true }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    await client.getTask("task/1");
    await client.getTaskLogs("task/1");
    expect(calls).toEqual([
      "http://localhost:8080/tasks/task%2F1",
      "http://localhost:8080/tasks/task%2F1/logs"
    ]);
  });

  it("requests runtime overview read models and provider inventory", async () => {
    const calls: string[] = [];
    const transport = new StubTransport(async (request) => {
      calls.push(request.url);
      return { status: 200, body: JSON.stringify({ items: [] }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    await client.getDashboardReadModel();
    await client.getAssistantReadModel();
    await client.listProviders();
    await client.listProviderCatalog();
    await client.getLlmBenchmarks("analysis", 5);
    expect(calls).toEqual([
      "http://localhost:8080/dashboard/read-model?benchmark_task_kind=analysis&include_task_snapshot=1",
      "http://localhost:8080/assistant/read-model",
      "http://localhost:8080/providers",
      "http://localhost:8080/providers/catalog",
      "http://localhost:8080/llm/benchmarks?task_kind=analysis&top_n=5"
    ]);
  });

  it("requests audit and repair visibility/detail endpoints", async () => {
    const calls: string[] = [];
    const transport = new StubTransport(async (request) => {
      calls.push(request.url);
      return { status: 200, body: JSON.stringify({ items: [] }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    await client.getAuditLogs(30, 0);
    await client.listRepairs();
    await client.getRepairSession("repair/1");
    expect(calls).toEqual([
      "http://localhost:8080/api/system/audit-logs?limit=30&offset=0",
      "http://localhost:8080/repairs",
      "http://localhost:8080/repairs/repair%2F1"
    ]);
  });

  it("sends approval action payloads to backend endpoints", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const transport = new StubTransport(async (request) => {
      calls.push({ url: request.url, body: JSON.parse(request.body ?? "{}") as Record<string, unknown> });
      return { status: 200, body: JSON.stringify({ updated: true }) };
    });
    const client = new AnantaBackendClient(settings, transport);
    await client.approveApproval("approval-1", "looks good");
    await client.rejectApproval("approval-1", "needs follow-up");
    expect(calls[0]?.url).toBe("http://localhost:8080/approvals/approval-1/approve");
    expect(calls[0]?.body.comment).toBe("looks good");
    expect(calls[1]?.url).toBe("http://localhost:8080/approvals/approval-1/reject");
    expect(calls[1]?.body.comment).toBe("needs follow-up");
  });
});
