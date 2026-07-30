import { adminGuard } from '../../guards/admin.guard';
import { modelAnalysisRoutes } from './model-analysis.routes';

describe('model analysis routes', () => {
  it('registers one lazy admin-governed feature boundary', () => {
    expect(modelAnalysisRoutes).toHaveLength(1);
    expect(modelAnalysisRoutes[0]).toMatchObject({
      path: 'model-analysis',
      canActivate: [adminGuard],
      data: { breadcrumb: 'Modellanalyse', area: 'Operate' },
    });
    expect(typeof modelAnalysisRoutes[0].loadComponent).toBe('function');
    expect(modelAnalysisRoutes[0].component).toBeUndefined();
  });
});
