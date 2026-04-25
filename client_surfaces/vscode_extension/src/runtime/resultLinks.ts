export interface RuntimeResultLink {
  label: string;
  url: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function toAbsolute(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalizedBase = baseUrl.replace(/\/+$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
}

export function buildResultLinks(baseUrl: string, data: unknown): RuntimeResultLink[] {
  const parsed = asRecord(data);
  if (!parsed) {
    return [
      { label: "Open Tasks", url: toAbsolute(baseUrl, "/tasks") },
      { label: "Open Artifacts", url: toAbsolute(baseUrl, "/artifacts") }
    ];
  }

  const links: RuntimeResultLink[] = [];
  const browserUrl = readString(parsed, "browser_url");
  const taskId = readString(parsed, "task_id");
  const goalId = readString(parsed, "goal_id");
  const artifactId = readString(parsed, "artifact_id");

  if (browserUrl) {
    links.push({ label: "Open Result", url: toAbsolute(baseUrl, browserUrl) });
  }
  if (goalId) {
    links.push({ label: "Open Goal", url: toAbsolute(baseUrl, `/goals/${encodeURIComponent(goalId)}`) });
  }
  if (taskId) {
    links.push({ label: "Open Task", url: toAbsolute(baseUrl, `/tasks/${encodeURIComponent(taskId)}`) });
    links.push({ label: "Open Task Artifacts", url: toAbsolute(baseUrl, `/artifacts?task_id=${encodeURIComponent(taskId)}`) });
  }
  if (artifactId) {
    links.push({ label: "Open Artifact", url: toAbsolute(baseUrl, `/artifacts/${encodeURIComponent(artifactId)}`) });
  }

  if (links.length === 0) {
    links.push({ label: "Open Tasks", url: toAbsolute(baseUrl, "/tasks") });
    links.push({ label: "Open Artifacts", url: toAbsolute(baseUrl, "/artifacts") });
  }
  return links;
}
