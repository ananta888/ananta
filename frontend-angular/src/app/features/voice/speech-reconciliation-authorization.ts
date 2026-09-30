/**
 * Time-bounded, generation-fenced record of the Hub authorization probe for
 * offline speech reconciliation. It only remembers which consent context was
 * authorized and until when; the facade decides what the context key is.
 */
export class SpeechReconciliationAuthorization {
  private authorizedContextKey = '';
  private generation = 0;
  private expiryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly onExpired: () => void) {}

  authorizedFor(contextKey: string): boolean {
    return contextKey === this.authorizedContextKey;
  }

  /**
   * Runs the Hub probe and records the authorization when neither a newer
   * attempt nor a context change (checked by `stillCurrent`) intervened.
   */
  async authorize(
    contextKey: string,
    probe: () => Promise<unknown>,
    stillCurrent: () => boolean,
    expiresAtMs: number,
  ): Promise<void> {
    const generation = ++this.generation;
    this.authorizedContextKey = '';
    await probe();
    if (generation !== this.generation || !stillCurrent()) {
      throw new Error('speech_reconciliation_authorization_stale');
    }
    this.authorizedContextKey = contextKey;
    this.armExpiry(expiresAtMs);
  }

  clear(): void {
    this.generation += 1;
    this.authorizedContextKey = '';
    if (this.expiryTimer !== null) {
      globalThis.clearTimeout(this.expiryTimer);
      this.expiryTimer = null;
    }
  }

  private armExpiry(expiresAtMs: number): void {
    if (this.expiryTimer !== null) {
      globalThis.clearTimeout(this.expiryTimer);
    }
    const delay = Math.max(1, Math.min(2_147_483_647, expiresAtMs - Date.now()));
    this.expiryTimer = globalThis.setTimeout(() => {
      this.clear();
      this.onExpired();
    }, delay);
  }
}
