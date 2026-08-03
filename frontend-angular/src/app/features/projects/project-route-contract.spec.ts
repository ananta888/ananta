import { describe, expect, it } from 'vitest';

import { adminRoutes } from '../admin/admin.routes';
import { controlPlaneRoutes } from '../control-plane/control-plane.routes';
import { organizationRoutes } from '../organizations/organization.routes';
import { systemRoutes } from '../system/system.routes';
import { taskRoutes } from '../tasks/task.routes';

describe('project-scoped route contract', () => {
  it.each([
    [controlPlaneRoutes, 'dashboard'],
    [taskRoutes, 'board'],
    [taskRoutes, 'goal/:id'],
    [adminRoutes, 'artifacts'],
    [systemRoutes, 'sources'],
    [organizationRoutes, 'organizations'],
  ])('marks %s/%s as project-scoped and guarded', (routes, path) => {
    const route = routes.find(candidate => candidate.path === path);

    expect(route).toBeDefined();
    expect(route?.data?.['projectScoped']).toBe(true);
    expect(route?.canActivate?.length).toBeGreaterThan(0);
  });
});
