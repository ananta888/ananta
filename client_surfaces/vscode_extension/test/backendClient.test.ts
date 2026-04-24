import { describe, expect, it } from "vitest";
import {
  AnantaBackendClient,
  HttpTransport,
  HttpTransportRequest,
  HttpTransportResponse
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
});
