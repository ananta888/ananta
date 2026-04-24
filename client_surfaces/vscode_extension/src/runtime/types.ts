export type AuthMode = "none" | "session_token" | "personal_token";

export type DegradedState =
  | "healthy"
  | "backend_unreachable"
  | "backend_timeout"
  | "auth_failed"
  | "policy_denied"
  | "capability_missing"
  | "stale_state"
  | "malformed_response"
  | "unknown_error";

export interface RuntimeSettings {
  baseUrl: string;
  profileId: string;
  runtimeTarget: string;
  authMode: AuthMode;
  authToken: string | null;
  timeoutMs: number;
  secretStorageKey: string;
}

export interface ResolvedRuntimeSettings {
  settings: RuntimeSettings | null;
  validationErrors: string[];
}

export interface ClientResponse<T> {
  ok: boolean;
  statusCode: number | null;
  state: DegradedState;
  data: T | null;
  error: string | null;
  retriable: boolean;
}
