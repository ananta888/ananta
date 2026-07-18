import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { GraphMetricCapability } from '../../models/graph-visual-metrics.model';
import { DEFAULT_GRAPH_VISUAL_PROFILE } from '../../models/graph-visual-profile.model';
import { GraphVisualProfileFacade } from '../../services/graph-visual-profile.facade';
import { GraphVisualProfileStoragePort } from '../../services/graph-visual-profile-storage.port';
import { GraphVisualSettingsComponent } from './graph-visual-settings.component';

class MemoryStorage implements GraphVisualProfileStoragePort {
  private value: string | null = null;
  load() { return { ok: true, value: this.value }; }
  save(_key: string, value: string) { this.value = value; return { ok: true, value }; }
  remove() { this.value = null; return { ok: true, value: null }; }
}

const CAPABILITIES: readonly GraphMetricCapability[] = [
  ...DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics.map(metric => ({
    metricId: metric.metricId,
    entity: 'node' as const,
    availability: metric.metricId === 'blast_radius' ? 'unavailable' as const : 'available' as const,
    source: 'worker', algorithmVersion: '1',
    reasonCode: metric.metricId === 'blast_radius' ? 'worker_artifact_missing' : undefined,
  })),
  ...DEFAULT_GRAPH_VISUAL_PROFILE.edgeMetrics.map(metric => ({
    metricId: metric.metricId, entity: 'edge' as const,
    availability: 'available' as const, source: 'worker', algorithmVersion: '1',
  })),
];

describe('GraphVisualSettingsComponent', () => {
  let fixture: ComponentFixture<GraphVisualSettingsComponent>;
  let component: GraphVisualSettingsComponent;
  let facade: GraphVisualProfileFacade;
  let callbacks: FrameRequestCallback[];

  beforeEach(async () => {
    callbacks = [];
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callbacks.push(callback);
      return callbacks.length;
    });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    await TestBed.configureTestingModule({
      imports: [GraphVisualSettingsComponent],
      providers: [{
        provide: GraphVisualProfileFacade,
        useFactory: () => new GraphVisualProfileFacade(new MemoryStorage()),
      }],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphVisualSettingsComponent);
    component = fixture.componentInstance;
    facade = TestBed.inject(GraphVisualProfileFacade);
    facade.load('test-graph');
    component.metricCapabilities = CAPABILITIES;
    component.open = true;
    fixture.detectChanges();
    callbacks = [];
  });

  afterEach(() => vi.unstubAllGlobals());

  it('renders typed controls for every node and edge metric', () => {
    const rows = fixture.nativeElement.querySelectorAll('.metric-row');
    expect(rows.length).toBe(
      DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics.length + DEFAULT_GRAPH_VISUAL_PROFILE.edgeMetrics.length,
    );
    expect(fixture.nativeElement.querySelector('[data-testid="graph-visual-profile-preset"]')).toBeTruthy();
  });

  it('keeps unavailable metrics visible and disabled with a stable reason code', () => {
    const row = fixture.nativeElement.querySelector('[data-metric-id="blast_radius"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.querySelector('input')?.disabled).toBe(true);
    expect(row.textContent).toContain('worker_artifact_missing');
  });

  it('coalesces ten slider updates into one validated projection per animation frame', () => {
    const activate = vi.spyOn(facade, 'activate');
    for (let index = 0; index < 10; index++) {
      component.patchHighlight('hover', 1 + index / 100);
    }
    expect(activate).not.toHaveBeenCalled();
    for (const callback of [...callbacks]) callback(16);
    expect(activate).toHaveBeenCalledTimes(1);
  });

  it('rejects an invalid range atomically without replacing the active profile', () => {
    const before = facade.activeProfile();
    component.patchRange('nodeSizeRange', 'min', 80);
    component.patchRange('nodeSizeRange', 'max', 2);
    for (const callback of [...callbacks]) callback(16);
    expect(facade.activeProfile()).toBe(before);
    expect(facade.lastIssues().some(issue => issue.reasonCode === 'range_min_greater_than_max')).toBe(true);
  });

  it('uses unique accessible drawer ids across component instances', () => {
    const second = TestBed.createComponent(GraphVisualSettingsComponent);
    second.componentInstance.open = true;
    second.detectChanges();
    expect(second.componentInstance.panelId).not.toBe(component.panelId);
    const trigger = fixture.nativeElement.querySelector('[data-testid="graph-visual-settings-trigger"]');
    expect(trigger.getAttribute('aria-controls')).toBe(component.panelId);
  });

  it('validates and applies a domain color override through the shared profile facade', () => {
    component.domainOptions = [{ domainId: 'agent/routes', label: 'agent/routes', color: '#112233' }];
    fixture.detectChanges();
    callbacks = [];
    const input = fixture.nativeElement.querySelector('input[type=color]') as HTMLInputElement;
    input.value = '#abcdef';
    input.dispatchEvent(new Event('change'));
    for (const callback of [...callbacks]) callback(16);
    expect(facade.activeProfile().domainColorOverrides['agent/routes']).toBe('#ABCDEF');
  });

  it('cancels a pending slider draft when a preset is selected atomically', () => {
    component.patchHighlight('hover', 2);
    const scheduled = [...callbacks];
    component.selectPreset('importance');
    for (const callback of scheduled) callback(16);
    expect(facade.activeProfile().profileId).toBe('importance');
    expect(facade.activeProfile().highlightFactors.hover).toBe(1.2);
  });
});
