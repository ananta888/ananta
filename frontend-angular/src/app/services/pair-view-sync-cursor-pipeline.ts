import type { CursorMessage } from './pair-view-sync-peer-cursors';

const CURSOR_INTERVAL_MS = 50;

/** What the cursor pipeline needs from the pair view sync. */
export interface PairCursorSendPort {
  /** Local consent for cursor sharing in the bound session. */
  sharingEnabled(): boolean;
  /** Seals and sends one cursor; `isCurrent` fences a superseded dispatch. */
  send(message: CursorMessage, isCurrent: () => boolean): Promise<boolean>;
  onSent(): void;
}

/**
 * Outgoing remote-cursor pipeline: keeps only the latest pointer, sends at
 * most one encrypted cursor per CURSOR_INTERVAL_MS with one dispatch in
 * flight, and drops every pending or in-flight cursor on cancel (consent
 * loss, epoch change, bind/unbind) through a send generation.
 */
export class PairCursorSendPipeline {
  private pendingMessage: CursorMessage | null = null;
  private sendInFlight = false;
  private sendGeneration = 0;
  private lastDispatchAt = 0;
  private dispatchHandle: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly port: PairCursorSendPort) {}

  submit(message: CursorMessage): void {
    this.pendingMessage = message;
    this.flush();
  }

  cancel(): void {
    this.pendingMessage = null;
    this.sendGeneration += 1;
    this.sendInFlight = false;
    if (this.dispatchHandle !== null) {
      clearTimeout(this.dispatchHandle);
      this.dispatchHandle = null;
    }
    this.lastDispatchAt = 0;
  }

  private flush(): void {
    if (this.sendInFlight || !this.pendingMessage) return;
    const waitMs = CURSOR_INTERVAL_MS - (Date.now() - this.lastDispatchAt);
    if (waitMs > 0) {
      if (this.dispatchHandle === null) {
        this.dispatchHandle = setTimeout(() => {
          this.dispatchHandle = null;
          this.flush();
        }, waitMs);
      }
      return;
    }
    const message = this.pendingMessage;
    this.pendingMessage = null;
    this.sendInFlight = true;
    this.lastDispatchAt = Date.now();
    const generation = this.sendGeneration;
    void this.port.send(
      message,
      () => generation === this.sendGeneration && this.port.sharingEnabled(),
    ).then(sent => {
      if (sent) {
        this.port.onSent();
      }
    }).finally(() => {
      if (generation !== this.sendGeneration) return;
      this.sendInFlight = false;
      if (this.pendingMessage) this.flush();
    });
  }
}
