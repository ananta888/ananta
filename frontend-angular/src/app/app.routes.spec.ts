import { routes } from './app.routes';
import { adminGuard } from './guards/admin.guard';

function flattenRoutes(items: typeof routes): any[] {
  return items.flatMap((route: any) => [
    route,
    ...flattenRoutes(route.children || []),
  ]);
}

describe('app routes', () => {
  it('lazy-loads feature views below the authenticated shell', () => {
    const lazyRoutes = flattenRoutes(routes).filter((route: any) => typeof route.loadComponent === 'function');
    const featureRoutes = lazyRoutes.filter((route: any) => [
      'dashboard',
      'operations',
      'auto-planner',
      'settings',
      'audit-log',
      'agents',
      'panel/:name',
      'webhooks',
      'board',
      'archived',
      'graph',
      'task/:id',
      'goal/:id',
      'templates',
      'teams',
      'artifacts',
      'markdown-slides',
      'strategy-game-demo',
      'help',
      'codehug',
    ].includes(route.path));

    expect(featureRoutes.length).toBe(22);
    expect(featureRoutes.every((route: any) => typeof route.loadComponent === 'function')).toBe(true);
    expect(featureRoutes.every((route: any) => !route.component)).toBe(true);
    expect(featureRoutes.every((route: any) => route.data?.breadcrumb && route.data?.area)).toBe(true);
  });

  it('lazy-loads the admin-guarded model training control center below the authenticated shell', () => {
    const route = flattenRoutes(routes).find((item: any) => item.path === 'model-training');

    expect(route).toBeTruthy();
    expect(route.component).toBeUndefined();
    expect(typeof route.loadComponent).toBe('function');
    expect(route.canActivate).toEqual([adminGuard]);
    expect(route.data).toEqual({ breadcrumb: 'Modelltraining', area: 'Configure' });
  });
});
