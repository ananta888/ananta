/**
 * Fail-closed primitive validators for semantic compute contract payloads.
 * Each throws a machine-readable error instead of coercing bad input.
 */

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("semantic_compute_response_invalid");
  return value as Record<string, unknown>;
}

export function exactRecord(
  value: unknown,
  fields: readonly string[],
  reason: string,
): Record<string, unknown> {
  const row = record(value);
  if (
    Object.keys(row).length !== fields.length ||
    fields.some((field) => !Object.hasOwn(row, field))
  ) {
    throw new Error(reason);
  }
  return row;
}

export function exactRecordWithOptional(
  value: unknown,
  required: readonly string[],
  optional: readonly string[],
  reason: string,
): Record<string, unknown> {
  const row = record(value);
  const allowed = new Set([...required, ...optional]);
  if (
    Object.keys(row).some((field) => !allowed.has(field)) ||
    required.some((field) => !Object.hasOwn(row, field))
  ) {
    throw new Error(reason);
  }
  return row;
}

export function identifier(value: unknown): string {
  const rendered = String(value ?? "");
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(rendered))
    throw new Error("semantic_compute_identifier_invalid");
  return rendered;
}

export function executorIdentifier(value: unknown): string {
  if (typeof value !== "string")
    throw new Error("semantic_compute_executor_id_invalid");
  if (/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value)) return value;
  if (
    value !== value.trim() ||
    new TextEncoder().encode(value).byteLength > 512 ||
    /\s/.test(value)
  ) {
    throw new Error("semantic_compute_executor_id_invalid");
  }
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== ""
    )
      throw new Error("unsafe");
  } catch {
    throw new Error("semantic_compute_executor_id_invalid");
  }
  return value;
}

export function digest(value: unknown): string {
  const rendered = String(value ?? "");
  if (!/^[a-f0-9]{64}$/.test(rendered))
    throw new Error("semantic_compute_digest_invalid");
  return rendered;
}

export function boundedText(
  value: unknown,
  minimum: number,
  maximumBytes: number,
): string {
  if (typeof value !== "string")
    throw new Error("semantic_compute_text_invalid");
  const bytes = new TextEncoder().encode(value).byteLength;
  if (bytes < minimum || bytes > maximumBytes)
    throw new Error("semantic_compute_text_invalid");
  return value;
}

export function boundedInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    Number(value) < minimum ||
    Number(value) > maximum
  ) {
    throw new Error("semantic_compute_integer_invalid");
  }
  return Number(value);
}

export function finiteNumber(
  value: unknown,
  minimum: number,
  maximum: number,
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new Error("semantic_compute_number_invalid");
  }
  return value;
}

export function positiveInteger(value: unknown): number {
  return boundedInteger(value, 1, Number.MAX_SAFE_INTEGER);
}
export function nonnegativeInteger(value: unknown): number {
  return boundedInteger(value, 0, Number.MAX_SAFE_INTEGER);
}

export function stringArray(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum)
    throw new Error("semantic_compute_array_invalid");
  return value.map(identifier);
}

export function parseBudget(value: unknown): {
  cpu_ms: number;
  memory_bytes: number;
  artifact_bytes: number;
} {
  const budget = record(value);
  if (
    Object.keys(budget).sort().join(",") !==
    "artifact_bytes,cpu_ms,memory_bytes"
  ) {
    throw new Error("semantic_compute_budget_invalid");
  }
  return {
    cpu_ms: boundedInteger(budget["cpu_ms"], 1, 60_000),
    memory_bytes: boundedInteger(budget["memory_bytes"], 1, 4_294_967_296),
    artifact_bytes: boundedInteger(budget["artifact_bytes"], 1, 4_194_304),
  };
}

export function enumValue<const T extends readonly string[]>(
  value: unknown,
  values: T,
): T[number] {
  if (!values.includes(value as T[number]))
    throw new Error("semantic_compute_enum_invalid");
  return value as T[number];
}

export function mutationKey(value: string): string {
  const rendered = String(value || "").trim();
  if (rendered.length < 8 || rendered.length > 256 || /\s/.test(rendered)) {
    throw new Error("semantic_compute_idempotency_key_invalid");
  }
  return rendered;
}
