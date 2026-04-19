export interface UiAsyncState<T> {
  data: T;
  loading: boolean;
  refreshing: boolean;
  empty: boolean;
  error: string | null;
  lastLoadedAt: number | null;
}

export function buildUiAsyncState<T>(
  data: T,
  options: {
    loading?: boolean;
    refreshing?: boolean;
    empty?: boolean;
    error?: string | null;
    lastLoadedAt?: number | null;
  } = {},
): UiAsyncState<T> {
  return {
    data,
    loading: Boolean(options.loading),
    refreshing: Boolean(options.refreshing),
    empty: Boolean(options.empty),
    error: options.error ?? null,
    lastLoadedAt: options.lastLoadedAt ?? null,
  };
}

export function isCollectionEmpty(value: unknown): boolean {
  return Array.isArray(value) ? value.length === 0 : value == null;
}
