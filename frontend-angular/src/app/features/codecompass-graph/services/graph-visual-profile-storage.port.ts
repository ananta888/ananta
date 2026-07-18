export interface GraphVisualProfileStorageResult {
  ok: boolean;
  value: string | null;
  reasonCode?: string;
}

/** Persistence boundary. Validation and active viewer state deliberately live elsewhere. */
export interface GraphVisualProfileStoragePort {
  load(contextKey: string): GraphVisualProfileStorageResult;
  save(contextKey: string, canonicalProfileJson: string): GraphVisualProfileStorageResult;
  remove(contextKey: string): GraphVisualProfileStorageResult;
}

export class InMemoryGraphVisualProfileStorageAdapter implements GraphVisualProfileStoragePort {
  private readonly values = new Map<string, string>();

  load(contextKey: string): GraphVisualProfileStorageResult {
    return { ok: true, value: this.values.get(contextKey) ?? null };
  }

  save(contextKey: string, canonicalProfileJson: string): GraphVisualProfileStorageResult {
    this.values.set(contextKey, canonicalProfileJson);
    return { ok: true, value: canonicalProfileJson };
  }

  remove(contextKey: string): GraphVisualProfileStorageResult {
    this.values.delete(contextKey);
    return { ok: true, value: null };
  }
}
