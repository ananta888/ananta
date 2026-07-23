import { CLOSED_DASHBOARD_FEATURE_FLAGS, decodeDashboardFeatureFlags } from './dashboard-feature-flags';

describe('dashboard feature flags', () => {
  it('fails closed for missing and string-valued flags', () => {
    expect(decodeDashboardFeatureFlags(undefined)).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(decodeDashboardFeatureFlags({
      schema: 'ananta.dashboard-feature-flags.v1',
      features: { angular_kanban: 'true', angular_model_dashboard: true },
    })).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
  });

  it('accepts only the versioned boolean projection', () => {
    expect(decodeDashboardFeatureFlags({
      status: 'success',
      data: {
        schema: 'ananta.dashboard-feature-flags.v1',
        features: { angular_kanban: true, angular_model_dashboard: false },
      },
    })).toEqual({ angularKanban: true, angularModelDashboard: false });
  });
});

