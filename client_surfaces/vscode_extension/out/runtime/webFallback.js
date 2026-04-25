"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.fallbackTargetLabel = fallbackTargetLabel;
exports.buildWebFallbackUrl = buildWebFallbackUrl;
const TARGET_PATHS = {
    tasks: "tasks",
    artifacts: "artifacts",
    audit: "audit",
    config: "config",
    repair: "repairs",
    goals: "goals"
};
function fallbackTargetLabel(target) {
    switch (target) {
        case "tasks":
            return "Tasks";
        case "artifacts":
            return "Artifacts";
        case "audit":
            return "Audit";
        case "config":
            return "Config";
        case "repair":
            return "Repair";
        case "goals":
            return "Goals";
    }
}
function safeBase(baseUrl) {
    try {
        const parsed = new URL(String(baseUrl || "").trim());
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
            return null;
        }
        parsed.pathname = "/";
        parsed.search = "";
        parsed.hash = "";
        return parsed;
    }
    catch {
        return null;
    }
}
function buildWebFallbackUrl(baseUrl, target, id = "", traceId = "") {
    const normalizedBase = safeBase(baseUrl);
    if (!normalizedBase) {
        return null;
    }
    const safeId = String(id || "").trim();
    const safeTrace = String(traceId || "").trim();
    const path = TARGET_PATHS[target];
    const suffix = safeId.length > 0 ? `/${encodeURIComponent(safeId)}` : "";
    normalizedBase.pathname = `/${path}${suffix}`;
    if (target === "audit" && safeTrace.length > 0) {
        normalizedBase.searchParams.set("trace", safeTrace);
    }
    return normalizedBase.toString();
}
//# sourceMappingURL=webFallback.js.map