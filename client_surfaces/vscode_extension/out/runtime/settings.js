"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.resolveRuntimeSettings = resolveRuntimeSettings;
const secretStore_1 = require("./secretStore");
const ALLOWED_AUTH_MODES = new Set(["none", "session_token", "personal_token"]);
function normalizeAuthMode(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!ALLOWED_AUTH_MODES.has(normalized)) {
        return null;
    }
    return normalized;
}
function isValidBaseUrl(value) {
    try {
        const parsed = new URL(value);
        return parsed.protocol === "http:" || parsed.protocol === "https:";
    }
    catch {
        return false;
    }
}
async function resolveRuntimeSettings(config, secretStore) {
    const baseUrl = String(config.get("baseUrl", "http://localhost:8080")).trim();
    const profileId = String(config.get("profileId", "default")).trim();
    const runtimeTarget = String(config.get("runtimeTarget", "local")).trim();
    const timeoutMs = Number(config.get("timeoutMs", 8000));
    const authModeRaw = String(config.get("auth.mode", "session_token")).trim();
    const secretStorageKey = String(config.get("auth.secretStorageKey", secretStore_1.DEFAULT_SECRET_STORAGE_KEY)).trim();
    const validationErrors = [];
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
    let authToken = null;
    if (effectiveAuthMode !== "none") {
        authToken = await secretStore.readToken(secretStorageKey || secretStore_1.DEFAULT_SECRET_STORAGE_KEY);
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
    const settings = {
        baseUrl,
        profileId,
        runtimeTarget,
        authMode: effectiveAuthMode,
        authToken,
        timeoutMs,
        secretStorageKey: secretStorageKey || secretStore_1.DEFAULT_SECRET_STORAGE_KEY
    };
    return {
        settings,
        validationErrors: []
    };
}
//# sourceMappingURL=settings.js.map