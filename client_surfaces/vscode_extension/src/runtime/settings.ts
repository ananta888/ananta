import { AnantaSecretStore, DEFAULT_SECRET_STORAGE_KEY } from "./secretStore";
import { AuthMode, ResolvedRuntimeSettings, RuntimeSettings } from "./types";

export interface ConfigurationReader {
  get<T>(key: string, defaultValue: T): T;
}

const ALLOWED_AUTH_MODES = new Set<AuthMode>(["none", "session_token", "personal_token"]);

function normalizeAuthMode(value: string): AuthMode | null {
  const normalized = String(value || "").trim().toLowerCase();
  if (!ALLOWED_AUTH_MODES.has(normalized as AuthMode)) {
    return null;
  }
  return normalized as AuthMode;
}

function isValidBaseUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export async function resolveRuntimeSettings(
  config: ConfigurationReader,
  secretStore: AnantaSecretStore
): Promise<ResolvedRuntimeSettings> {
  const baseUrl = String(config.get("baseUrl", "http://localhost:8080")).trim();
  const profileId = String(config.get("profileId", "default")).trim();
  const runtimeTarget = String(config.get("runtimeTarget", "local")).trim();
  const timeoutMs = Number(config.get("timeoutMs", 8000));
  const authModeRaw = String(config.get("auth.mode", "session_token")).trim();
  const secretStorageKey = String(config.get("auth.secretStorageKey", DEFAULT_SECRET_STORAGE_KEY)).trim();

  const validationErrors: string[] = [];
  const authMode = normalizeAuthMode(authModeRaw);
  if (!authMode) {
    validationErrors.push(`invalid_auth_mode:${authModeRaw}`);
  }
  if (!isValidBaseUrl(baseUrl)) {
    validationErrors.push(`invalid_base_url:${baseUrl}`);
  }
  if (!profileId) {
    validationErrors.push("profile_id_required");
  }
  if (!runtimeTarget) {
    validationErrors.push("runtime_target_required");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1000) {
    validationErrors.push("timeout_ms_invalid");
  }

  const effectiveAuthMode = authMode ?? "none";
  let authToken: string | null = null;
  if (effectiveAuthMode !== "none") {
    authToken = await secretStore.readToken(secretStorageKey || DEFAULT_SECRET_STORAGE_KEY);
    if (!authToken) {
      validationErrors.push("missing_auth_token");
    }
  }

  if (validationErrors.length > 0) {
    return {
      settings: null,
      validationErrors
    };
  }

  const settings: RuntimeSettings = {
    baseUrl,
    profileId,
    runtimeTarget,
    authMode: effectiveAuthMode,
    authToken,
    timeoutMs,
    secretStorageKey: secretStorageKey || DEFAULT_SECRET_STORAGE_KEY
  };
  return {
    settings,
    validationErrors: []
  };
}
