import { describe, expect, it } from 'vitest';

import { visualProcessRoutes } from './visual-process.routes';

describe('visualProcessRoutes', () => {
  it('registers the BPMN blueprint editor route', async () => {
    const route = visualProcessRoutes.find(item => item.path === 'process-designer/bpmn');

    expect(route?.data?.['breadcrumb']).toBe('BPMN Blueprint Editor');
    expect(route?.loadComponent).toBeTypeOf('function');

    const component = await route?.loadComponent?.();
    expect(component).toBeTruthy();
  });
});
