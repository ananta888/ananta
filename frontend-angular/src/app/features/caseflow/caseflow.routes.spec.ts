import { describe, expect, it } from 'vitest';

import { buildNavGroups } from '../../models/route-metadata';
import { caseFlowRoutes } from './caseflow.routes';

describe('CaseFlow Studio route', () => {
  it('maps the single CaseFlow Studio menu entry to the lazy Studio component', async () => {
    const menuEntries = buildNavGroups('user', 'simple')
      .flatMap(group => group.items)
      .filter(item => item.path === '/caseflow/studio');
    const caseFlow = caseFlowRoutes.find(route => route.path === 'caseflow');
    const studio = caseFlow?.children?.find(route => route.path === 'studio');

    expect(menuEntries).toHaveLength(1);
    expect(menuEntries[0]).toMatchObject({
      label: 'CaseFlow Studio',
      navGroup: 'CaseFlow',
    });
    expect(studio?.data?.['breadcrumb']).toBe('CaseFlow Studio');
    expect(studio?.canDeactivate).toHaveLength(1);
    expect(studio?.loadComponent).toBeTypeOf('function');

    const component = await studio?.loadComponent?.();
    expect(component?.name).toBe('CaseFlowStudioComponent');
  });
});
