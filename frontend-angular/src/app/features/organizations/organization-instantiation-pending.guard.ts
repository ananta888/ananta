import { CanDeactivateFn } from '@angular/router';

export interface OrganizationInstantiationDeactivationAware {
  canLeaveOrganizations(): boolean;
}

export const organizationInstantiationPendingGuard:
CanDeactivateFn<OrganizationInstantiationDeactivationAware> = (
  component,
  _currentRoute,
  _currentState,
  nextState,
) => (
  isAuthenticationTarget(nextState.url)
  || component.canLeaveOrganizations()
);

function isAuthenticationTarget(url: string): boolean {
  const path = String(url || '')
    .split(/[?#]/, 1)[0]
    .replace(/\/+$/, '') || '/';
  return path === '/login' || path === '/oidc-callback';
}
