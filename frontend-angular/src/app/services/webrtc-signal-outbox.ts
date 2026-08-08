/** Serializes signaling writes and fences queued work across session changes. */
export class WebrtcSignalOutbox {
  private generation = 0;
  private tail: Promise<void> = Promise.resolve();
  private activeWrites = 0;

  get hasInFlightWrite(): boolean {
    return this.activeWrites > 0;
  }

  reset(): void {
    this.generation += 1;
    this.tail = Promise.resolve();
  }

  enqueue(operation: () => Promise<void>): Promise<void> {
    const generation = this.generation;
    const queued = this.tail.then(async () => {
      if (generation !== this.generation) throw new Error('webrtc_signal_outbox_stale');
      this.activeWrites += 1;
      try {
        await operation();
        if (generation !== this.generation) throw new Error('webrtc_signal_outbox_stale');
      } finally {
        this.activeWrites -= 1;
      }
    });
    // A rejected write poisons this generation: later queued ICE/SDP must not
    // overtake or continue after the missing server sequence.
    this.tail = queued;
    return queued;
  }
}
