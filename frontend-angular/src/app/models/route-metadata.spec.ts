import { APP_ROUTE_META, buildNavGroups } from './route-metadata';

describe('route metadata', () => {
  it('drives nav groups from declared route metadata', () => {
    const adminGroups = buildNavGroups('admin', 'advanced');

    expect(adminGroups.map(group => group.label)).toEqual([
      'Arbeiten',
      'CaseFlow',
      'Betrieb',
      'Automatisierung',
      'Konfiguration',
    ]);
    expect(adminGroups.flatMap(group => group.items).map(item => item.path)).toContain('/audit-log');
    expect(APP_ROUTE_META.dashboard.area).toBe('Operate');
  });

  it('keeps simple navigation focused on everyday work', () => {
    const simplePaths = buildNavGroups('admin', 'simple').flatMap(group => group.items).map(item => item.path);

    expect(simplePaths).toEqual([
      '/codehug',
      '/projects',
      '/workspace',
      '/chats',
      '/board',
      '/sources',
      '/artifacts',
      '/markdown-slides',
      '/templates',
      '/voice',
      '/voxtral-offline',
      '/llama-runtime',
      '/help',
      '/caseflow/studio',
    ]);
    expect(simplePaths).not.toContain('/agents');
    expect(simplePaths).not.toContain('/audit-log');
  });

  it('exposes projects and source indexing as unambiguous everyday navigation', () => {
    const workItems = buildNavGroups('user', 'simple')
      .find(group => group.label === 'Arbeiten')?.items || [];

    expect(workItems).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: '/projects', label: 'Projekte' }),
      expect.objectContaining({ path: '/sources', label: 'Quellen & Indexierung' }),
    ]));
  });

  it('places CaseFlow Studio first in its own main group with a separator', () => {
    const caseFlowGroup = buildNavGroups('admin', 'simple')
      .find(group => group.label === 'CaseFlow');

    expect(caseFlowGroup?.items).toEqual([
      expect.objectContaining({
        label: 'CaseFlow Studio',
        path: '/caseflow/studio',
        separatorAfter: true,
      }),
    ]);
  });

  it('hides admin-only navigation for non-admin users', () => {
    const userPaths = buildNavGroups('user', 'advanced').flatMap(group => group.items).map(item => item.path);

    expect(userPaths).not.toContain('/audit-log');
    expect(userPaths).not.toContain('/model-training');
    expect(userPaths).toContain('/settings');
  });

  it('exposes model training only to admins in advanced navigation', () => {
    const adminAdvanced = buildNavGroups('admin', 'advanced').flatMap(group => group.items).map(item => item.path);
    const adminSimple = buildNavGroups('admin', 'simple').flatMap(group => group.items).map(item => item.path);

    expect(adminAdvanced).toContain('/model-training');
    expect(adminSimple).not.toContain('/model-training');
    expect(APP_ROUTE_META['model-training']).toMatchObject({ adminOnly: true, expertOnly: true, area: 'Configure' });
  });

  it('shows the workflow and blueprint config workbenches next to the config graph in advanced navigation', () => {
    const configItems = buildNavGroups('admin', 'advanced')
      .find(group => group.label === 'Konfiguration')?.items || [];
    const paths = configItems.map(item => item.path);

    expect(paths).toContain('/effective-workflow');
    expect(paths).toContain('/config-graph');
    expect(paths).toContain('/blueprint-config');
    expect(paths.indexOf('/effective-workflow')).toBe(paths.indexOf('/config-graph') - 1);
    expect(paths.indexOf('/blueprint-config')).toBe(paths.indexOf('/config-graph') + 1);
  });
});
