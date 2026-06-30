import { routes } from './app.routes';

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
});
