export type SendOperationState = 'queued' | 'sending' | 'acknowledged' | 'timed_out' | 'cancelled' | 'disconnected';

export interface SendReceipt {
  sessionId: string;
  epoch: number;
  messageDigest: string;
  state: SendOperationState;
  ackCursor?: number;
}

export class WebrtcSendOperation {
  readonly result: Promise<SendReceipt>;
  private resolveResult!: (receipt: SendReceipt) => void;
  private settled = false;
  private highestAckCursor = -1;
  private timeoutHandle: ReturnType<typeof setTimeout> | null = null;
  private readonly onAbort = () => this.finish('cancelled');

  constructor(
    readonly sessionId: string,
    readonly epoch: number,
    readonly messageDigest: string,
    readonly deadlineMs: number,
    private readonly signal?: AbortSignal,
    private readonly clock: () => number = () => Date.now(),
  ) {
    if (!sessionId || !Number.isSafeInteger(epoch) || epoch < 1 || !/^[0-9a-f]{64}$/.test(messageDigest)) {
      throw new Error('invalid_send_operation_identity');
    }
    if (!Number.isFinite(deadlineMs) || deadlineMs <= this.clock()) throw new Error('invalid_send_operation_deadline');
    this.result = new Promise(resolve => { this.resolveResult = resolve; });
    if (signal?.aborted) {
      this.finish('cancelled');
      return;
    }
    signal?.addEventListener('abort', this.onAbort, { once: true });
    this.timeoutHandle = setTimeout(() => this.finish('timed_out'), Math.max(0, deadlineMs - this.clock()));
  }

  markSending(): void {
    if (this.settled || this.clock() >= this.deadlineMs) this.finish('timed_out');
  }

  acknowledge(cursor?: number): void {
    if (this.settled) return;
    if (cursor !== undefined) {
      if (!Number.isSafeInteger(cursor) || cursor < this.highestAckCursor) return;
      this.highestAckCursor = cursor;
    }
    this.finish('acknowledged', cursor);
  }

  cancel(): void { this.finish('cancelled'); }
  disconnect(): void { this.finish('disconnected'); }

  private finish(state: SendOperationState, ackCursor?: number): void {
    if (this.settled) return;
    this.settled = true;
    if (this.timeoutHandle) clearTimeout(this.timeoutHandle);
    this.timeoutHandle = null;
    this.signal?.removeEventListener('abort', this.onAbort);
    this.resolveResult({
      sessionId: this.sessionId,
      epoch: this.epoch,
      messageDigest: this.messageDigest,
      state,
      ...(ackCursor === undefined ? {} : { ackCursor }),
    });
  }
}
