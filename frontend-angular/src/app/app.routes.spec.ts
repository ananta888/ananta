import { routes } from './app.routes';
import { adminGuard } from './guards/admin.guard';
import { authGuard } from './auth.guard';
import { publicPairGuard } from './guards/public-pair.guard';

function flattenRoutes(items: typeof routes): any[] {
  return items.flatMap((route: any) => [
    route,
    ...flattenRoutes(route.children || []),
  ]);
}

describe('app routes', () => {
  it('keeps Public Pair outside the Hub-authenticated route tree', () => {
    const pairRoute = routes.find((route) => route.path === 'pair-dev');
    const hubShell = routes.find((route) => route.path === '' && route.children);

    expect(pairRoute?.canActivate).toEqual([publicPairGuard]);
    expect(pairRoute?.canDeactivate).toBeUndefined();
    expect(typeof pairRoute?.loadComponent).toBe('function');
    expect(hubShell?.canActivate).toEqual([authGuard]);
    expect(hubShell?.children?.some((route) => route.path === 'pair-dev')).toBe(false);
  });

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

  it('groups application scenarios below CaseFlow and keeps the classroom link compatible', () => {
    const flat = flattenRoutes(routes);
    const caseFlow = flat.find((item: any) => item.path === 'caseflow');
    const classroomRedirect = flat.find((item: any) => item.path === 'classroom');

    expect(caseFlow).toBeTruthy();
    expect(caseFlow.loadComponent).toBeUndefined();
    expect(caseFlow.children.map((item: any) => item.path)).toEqual([
      '',
      'studio',
      'classroom',
      'jobs',
      'scenario/:scenarioId',
    ]);
    expect(classroomRedirect.redirectTo).toBe('caseflow/classroom');
  });
});
