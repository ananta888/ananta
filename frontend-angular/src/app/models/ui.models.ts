export interface UiAsyncState {
  loading: boolean;
  error: string | null;
  empty: boolean;
}

// Re-export: DashboardReadModel lebt jetzt vollständig in dashboard.models.ts.
export type { DashboardReadModel } from './dashboard.models';
