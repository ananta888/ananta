export type WebFallbackTarget = "tasks" | "artifacts" | "audit" | "config" | "repair" | "goals";

const TARGET_PATHS: Record<WebFallbackTarget, string> = {
  tasks: "tasks",
  artifacts: "artifacts",
  audit: "audit",
  config: "config",
  repair: "repairs",
  goals: "goals"
};

export function fallbackTargetLabel(target: WebFallbackTarget): string {
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

function safeBase(baseUrl: string): URL | null {
  try {
    const parsed = new URL(String(baseUrl || "").trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    parsed.pathname = "/";
    parsed.search = "";
    parsed.hash = "";
    return parsed;
  } catch {
    return null;
  }
}

export function buildWebFallbackUrl(baseUrl: string, target: WebFallbackTarget, id = "", traceId = ""): string | null {
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
