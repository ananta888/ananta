export interface UiAsyncState {
  loading: boolean;
  error: string | null;
  empty: boolean;
}

export interface DashboardReadModel {
  teams?: { count?: number; items?: any[] };
  roles?: { count?: number; items?: any[] };
  templates?: { count?: number; items?: any[] };
  agents?: { count?: number; items?: any[] };
  tasks?: { counts?: Record<string, number>; recent?: any[] };
  benchmarks?: { updated_at?: number | null; items?: any[] };
  context_timestamp?: number;
}
