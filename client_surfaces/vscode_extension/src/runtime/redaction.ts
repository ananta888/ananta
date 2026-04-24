const SECRET_KEY_HINTS = [
  "token",
  "secret",
  "password",
  "api_key",
  "apikey",
  "authorization"
];

const REDACTION_PATTERNS: Array<[RegExp, string]> = [
  [/(bearer\s+)[A-Za-z0-9._-]{6,}/gi, "$1***"],
  [/(token|secret|password|api[_-]?key)\s*[:=]\s*([^\s,;]+)/gi, "$1=***"],
  [/[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, "***jwt***"]
];

export function containsSecretKey(key: string): boolean {
  const lowered = String(key || "").toLowerCase();
  return SECRET_KEY_HINTS.some((hint) => lowered.includes(hint));
}

export function redactSensitiveText(input: string): string {
  let output = String(input ?? "");
  for (const [pattern, replacement] of REDACTION_PATTERNS) {
    output = output.replace(pattern, replacement);
  }
  return output;
}

export function sanitizeErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "unknown_error");
  return redactSensitiveText(raw);
}
