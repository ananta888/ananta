export interface DialogStartReceipt {
  schema: string; task_id: string; session_id: string; status: string;
}

function freezeJson(value: unknown): unknown {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach(freezeJson);
    Object.freeze(value);
  }
  return value;
}

/** Allocate once per explicit command, never once per observable subscription. */
export function dialogStartRequest(body: unknown): { body: unknown; headers: Record<string, string> } {
  try {
    const raw = JSON.stringify(body);
    if (!raw || new TextEncoder().encode(raw).byteLength > 2048) throw new Error();
    const key = crypto.randomUUID();
    if (!/^[a-f0-9-]{36}$/.test(key)) throw new Error();
    return { body: freezeJson(JSON.parse(raw)), headers: Object.freeze({ 'Idempotency-Key': key }) };
  } catch {
    throw new Error('meet_dialog_start_request_invalid');
  }
}
