import { APP_ROUTE_META, buildNavGroups } from './route-metadata';

describe('route metadata', () => {
  it('drives nav groups from declared route metadata', () => {
    const adminGroups = buildNavGroups('admin', 'advanced');

    expect(adminGroups.map(group => group.label)).toEqual(['Arbeiten', 'Betrieb', 'Automatisierung', 'Konfiguration']);
    expect(adminGroups.flatMap(group => group.items).map(item => item.path)).toContain('/audit-log');
    expect(APP_ROUTE_META.dashboard.area).toBe('Operate');
  });

  it('keeps simple navigation focused on everyday work', () => {
    const simplePaths = buildNavGroups('admin', 'simple').flatMap(group => group.items).map(item => item.path);

    expect(simplePaths).toEqual(['/dashboard', '/board', '/artifacts', '/templates', '/help']);
    expect(simplePaths).not.toContain('/agents');
    expect(simplePaths).not.toContain('/audit-log');
  });

  it('hides admin-only navigation for non-admin users', () => {
    const userPaths = buildNavGroups('user', 'advanced').flatMap(group => group.items).map(item => item.path);

    expect(userPaths).not.toContain('/audit-log');
    expect(userPaths).toContain('/settings');
  });
});
