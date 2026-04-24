import { ClientResponse, DegradedState, RuntimeSettings } from "./types";

export interface HttpTransportRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string | null;
  timeoutMs: number;
}

export interface HttpTransportResponse {
  status: number;
  body: string;
}

export interface HttpTransport {
  request(request: HttpTransportRequest): Promise<HttpTransportResponse>;
}

export class FetchHttpTransport implements HttpTransport {
  public async request(request: HttpTransportRequest): Promise<HttpTransportResponse> {
    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), request.timeoutMs);
    try {
      const response = await fetch(request.url, {
        method: request.method,
        headers: request.headers,
        body: request.body ?? undefined,
        signal: controller.signal
      });
      const body = await response.text();
      return {
        status: response.status,
        body
      };
    } finally {
      clearTimeout(timeoutHandle);
    }
  }
}

function mapStatusToState(statusCode: number, parseError: boolean): DegradedState {
  if (parseError) {
    return "malformed_response";
  }
  if (statusCode >= 200 && statusCode < 300) {
    return "healthy";
  }
  if (statusCode === 401) {
    return "auth_failed";
  }
  if (statusCode === 403) {
    return "policy_denied";
  }
  if (statusCode === 404 || statusCode === 422) {
    return "capability_missing";
  }
  if (statusCode === 408 || statusCode === 429) {
    return "backend_timeout";
  }
  if (statusCode === 409) {
    return "stale_state";
  }
  if (statusCode >= 500) {
    return "backend_unreachable";
  }
  return "unknown_error";
}

function isRetriableState(state: DegradedState): boolean {
  return state === "backend_timeout" || state === "backend_unreachable";
}

export class AnantaBackendClient {
  public constructor(
    private readonly settings: RuntimeSettings,
    private readonly transport: HttpTransport = new FetchHttpTransport()
  ) {}

  private async requestJson<T>(method: string, path: string, payload?: unknown): Promise<ClientResponse<T>> {
    const url = `${this.settings.baseUrl.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json"
    };
    if (this.settings.authToken) {
      headers.Authorization = `Bearer ${this.settings.authToken}`;
    }
    const body = payload === undefined ? null : JSON.stringify(payload);

    let statusCode: number | null = null;
    let rawBody = "";
    try {
      const response = await this.transport.request({
        method,
        url,
        headers,
        body,
        timeoutMs: this.settings.timeoutMs
      });
      statusCode = response.status;
      rawBody = response.body ?? "";
    } catch {
      return {
        ok: false,
        statusCode: null,
        state: "backend_unreachable",
        data: null,
        error: "request_failed:backend_unreachable",
        retriable: true
      };
    }

    let parseError = false;
    let parsed: T | null = null;
    if (rawBody.trim().length > 0) {
      try {
        parsed = JSON.parse(rawBody) as T;
      } catch {
        parseError = true;
      }
    }

    const state = mapStatusToState(statusCode, parseError);
    return {
      ok: state === "healthy",
      statusCode,
      state,
      data: parsed,
      error: state === "healthy" ? null : `request_failed:${state}`,
      retriable: isRetriableState(state)
    };
  }

  public getHealth(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/health");
  }

  public getCapabilities(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/capabilities");
  }

  public listGoals(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/goals");
  }

  public listTasks(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/tasks");
  }

  public listArtifacts(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/artifacts");
  }

  public listApprovals(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/approvals");
  }

  public getAuditLogs(limit = 30, offset = 0): Promise<ClientResponse<Record<string, unknown>>> {
    const safeLimit = Math.max(1, Math.trunc(limit));
    const safeOffset = Math.max(0, Math.trunc(offset));
    return this.requestJson("GET", `/api/system/audit-logs?limit=${safeLimit}&offset=${safeOffset}`);
  }

  public listRepairs(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/repairs");
  }

  public getConfig(): Promise<ClientResponse<Record<string, unknown>>> {
    return this.requestJson("GET", "/config");
  }
}
