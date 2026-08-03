import { ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';

import {
  OrganizationInstantiationDeactivationAware,
  organizationInstantiationPendingGuard,
} from './organization-instantiation-pending.guard';

describe('organizationInstantiationPendingGuard', () => {
  it.each([
    '/login?sphere=hub',
    '/oidc-callback?code=authorization-code',
  ])('always permits the security-relevant authentication target %s', (url) => {
    const canLeaveOrganizations = vi.fn(() => false);
    const component: OrganizationInstantiationDeactivationAware = {
      canLeaveOrganizations,
    };

    const result = execute(component, url);

    expect(result).toBe(true);
    expect(canLeaveOrganizations).not.toHaveBeenCalled();
  });

  it('blocks ordinary navigation while instantiation is pending', () => {
    const component: OrganizationInstantiationDeactivationAware = {
      canLeaveOrganizations: vi.fn(() => false),
    };

    const result = execute(component, '/dashboard?projectId=project-alpha');

    expect(result).toBe(false);
    expect(component.canLeaveOrganizations).toHaveBeenCalledOnce();
  });

  it('permits ordinary navigation when the organization flow is safe to leave', () => {
    const component: OrganizationInstantiationDeactivationAware = {
      canLeaveOrganizations: vi.fn(() => true),
    };

    const result = execute(component, '/dashboard');

    expect(result).toBe(true);
    expect(component.canLeaveOrganizations).toHaveBeenCalledOnce();
  });
});

function execute(
  component: OrganizationInstantiationDeactivationAware,
  nextUrl: string,
) {
  return organizationInstantiationPendingGuard(
    component,
    {} as ActivatedRouteSnapshot,
    {} as RouterStateSnapshot,
    { url: nextUrl } as RouterStateSnapshot,
  );
}
