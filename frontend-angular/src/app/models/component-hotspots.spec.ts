import { COMPONENT_HOTSPOTS, highestPriorityHotspots } from './component-hotspots';

describe('component hotspot inventory', () => {
  it('tracks the largest remaining smart-component refactoring candidates', () => {
    expect(COMPONENT_HOTSPOTS.length).toBeGreaterThanOrEqual(4);
    expect(COMPONENT_HOTSPOTS[0]).toEqual(expect.objectContaining({
      path: 'frontend-angular/src/app/components/settings.component.ts',
      priority: 'critical',
    }));
    expect(COMPONENT_HOTSPOTS.every(item => item.lines >= 1000)).toBe(true);
  });

  it('exposes a prioritized follow-up list', () => {
    expect(highestPriorityHotspots(2).map(item => item.path)).toEqual([
      'frontend-angular/src/app/components/settings.component.ts',
      'frontend-angular/src/app/components/artifacts.component.ts',
    ]);
  });
});
