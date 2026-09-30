/**
 * DataChannel policy gates of a WebRTC session (T22): allowed legacy
 * message types, the sliding-window receive rate/byte limit, per-channel
 * receive queue bounds and the send traffic class of legacy messages.
 */

export const ALLOWED_DC_TYPES: ReadonlySet<string> = new Set([
  'hello', 'hello_ack', 'ping', 'pong', 'chat', 'view_payload', 'cursor', 'artifact', 'control', 'chunk', 'error',
]);
const RATE_LIMIT_WINDOW_MS = 1000;
const RATE_LIMIT_MAX = 300;
const RATE_LIMIT_BYTES = 4 * 1024 * 1024;
export const DC_RECEIVE_QUEUE_MAX = 128;
export const DC_RECEIVE_QUEUE_BYTES = 4 * 1024 * 1024;

/** Byte size used for admission; non-string frames are always over budget. */
export function dcMessageBytes(raw: unknown): number {
  return typeof raw === 'string'
    ? new TextEncoder().encode(raw).byteLength
    : RATE_LIMIT_BYTES + 1;
}

export function legacyDcTrafficClass(type: string): 'evidence_bulk' | 'transcript' | 'visual_semantic' | 'control' {
  return type === 'artifact' ? 'evidence_bulk'
    : type === 'chat' || type === 'cursor' ? 'transcript'
      : type === 'view_payload' ? 'visual_semantic' : 'control';
}

/** Sliding one-second window over message count and bytes for one session service. */
export class DcReceiveRateLimiter {
  private rateTs: number[] = [];
  private rateBytes: Array<{ ts: number; bytes: number }> = [];

  /** Returns false when the window is exhausted; the frame is then not recorded. */
  admit(incomingBytes: number, now: number): boolean {
    this.rateTs = this.rateTs.filter(timestamp => now - timestamp < RATE_LIMIT_WINDOW_MS);
    this.rateBytes = this.rateBytes.filter(row => now - row.ts < RATE_LIMIT_WINDOW_MS);
    const windowBytes = this.rateBytes.reduce((sum, row) => sum + row.bytes, 0);
    if (this.rateTs.length >= RATE_LIMIT_MAX || windowBytes + incomingBytes > RATE_LIMIT_BYTES) {
      return false;
    }
    this.rateTs.push(now);
    this.rateBytes.push({ ts: now, bytes: incomingBytes });
    return true;
  }
}
