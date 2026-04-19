import { routes } from './app.routes';

function flattenRoutes(items: typeof routes): any[] {
  return items.flatMap((route: any) => [
    route,
    ...flattenRoutes(route.children || []),
  ]);
}

describe('app routes', () => {
  it('lazy-loads feature views below the authenticated shell', () => {
    const featureRoutes = flattenRoutes(routes).filter((route: any) => [
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
    ].includes(route.path));

    expect(featureRoutes.length).toBe(16);
    expect(featureRoutes.every((route: any) => typeof route.loadComponent === 'function')).toBe(true);
    expect(featureRoutes.every((route: any) => !route.component)).toBe(true);
  });
});
