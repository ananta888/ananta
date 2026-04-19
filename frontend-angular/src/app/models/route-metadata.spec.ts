import { APP_ROUTE_META, buildNavGroups } from './route-metadata';

describe('route metadata', () => {
  it('drives nav groups from declared route metadata', () => {
    const adminGroups = buildNavGroups('admin');

    expect(adminGroups.map(group => group.label)).toEqual(['Betrieb', 'Automatisierung', 'Konfiguration']);
    expect(adminGroups.flatMap(group => group.items).map(item => item.path)).toContain('/audit-log');
    expect(APP_ROUTE_META.dashboard.area).toBe('Operate');
  });

  it('hides admin-only navigation for non-admin users', () => {
    const userPaths = buildNavGroups('user').flatMap(group => group.items).map(item => item.path);

    expect(userPaths).not.toContain('/audit-log');
    expect(userPaths).toContain('/settings');
  });
});
