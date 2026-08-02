import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { OrganizationTopologyNode } from '../models/organization-topology.models';
import { OrganizationGraphComponent } from './organization-graph/organization-graph.component';
import { DefaultCanvasViewportAdapter } from './organization-graph/canvas-viewport.port';
import { OrganizationHierarchyComponent } from './organization-hierarchy/organization-hierarchy.component';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

const nodes: readonly OrganizationTopologyNode[] = [
  {
    id: 'org-1', stable_key: 'org-1', kind: 'organization', label: 'Medium Organization',
    parent_id: null, depth: 0, child_count: 2,
  },
  {
    id: 'team-a', stable_key: 'delivery-a', kind: 'team', label: 'Delivery A',
    parent_id: 'org-1', depth: 1, child_count: 0,
  },
  {
    id: 'team-b', stable_key: 'quality', kind: 'team', label: 'Quality',
    parent_id: 'org-1', depth: 1, child_count: 0,
  },
];

function fakeState() {
  const selectedNodeId = signal<string | null>(null);
  const selectedEdgeId = signal<string | null>(null);
  const visibleNodes = signal(nodes);
  const visibleEdges = signal([
    { id: 'edge-a', namespace: 'hierarchy' as const, kind: 'contains' as const, source_id: 'org-1', target_id: 'team-a' },
    { id: 'edge-b', namespace: 'hierarchy' as const, kind: 'contains' as const, source_id: 'org-1', target_id: 'team-b' },
  ]);
  return {
    selectedNodeId,
    selectedEdgeId,
    visibleNodes,
    visibleEdges,
    loading: signal(false),
    statusText: signal('3 Knoten und 2 Kanten'),
    canLoadMore: signal(false),
    topology: signal({
      truncated: false,
      runtime_overlay: {
        nodes: [
          { node_id: 'team-a', status: { state: 'active', label: 'aktiv' } },
          { node_id: 'team-b', status: { state: 'blocked', label: 'blockiert' } },
        ],
      },
    }),
    layoutPreferences: signal(new Map()),
    selectNode: vi.fn((id: string | null) => selectedNodeId.set(id)),
    selectEdge: vi.fn((id: string | null) => selectedEdgeId.set(id)),
    loadChildren: vi.fn(),
    loadMore: vi.fn(),
    saveLayout: vi.fn(),
    updateLayout: vi.fn(),
    setFocus: vi.fn(),
  };
}

describe('organization topology components', () => {
  it('keeps tree semantics, screen-reader status text, and arrow-key focus', async () => {
    const state = fakeState();
    TestBed.configureTestingModule({
      imports: [OrganizationHierarchyComponent],
      providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
    });
    const fixture = TestBed.createComponent(OrganizationHierarchyComponent);
    fixture.detectChanges();
    const tree = fixture.nativeElement.querySelector('[role="tree"]') as HTMLElement;
    const items = fixture.nativeElement.querySelectorAll('[role="treeitem"]') as NodeListOf<HTMLButtonElement>;

    expect(tree.getAttribute('aria-label')).toContain('Organisation');
    expect(items.length).toBe(3);
    expect(items[1].textContent).toContain('aktiv');
    expect(items[2].textContent).toContain('blockiert');
    items[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    fixture.detectChanges();
    await Promise.resolve();

    expect(state.selectNode).toHaveBeenCalledWith('team-a');
    expect(items[1].tabIndex).toBe(0);
  });

  it('renders status as text and exposes graph keyboard instructions', () => {
    const state = fakeState();
    TestBed.configureTestingModule({
      imports: [OrganizationGraphComponent],
      providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
    });
    const fixture = TestBed.createComponent(OrganizationGraphComponent);
    fixture.detectChanges();
    const canvas = fixture.nativeElement.querySelector('[role="application"]') as HTMLElement;

    expect(canvas.getAttribute('aria-label')).toContain('Pfeiltasten');
    expect(fixture.nativeElement.textContent).toContain('aktiv');
    expect(fixture.nativeElement.textContent).toContain('blockiert');
    expect(fixture.nativeElement.querySelectorAll('.node').length).toBe(3);
    const edge = fixture.nativeElement.querySelector('.edge-control') as SVGGElement;
    expect(edge.getAttribute('aria-label')).toContain('contains');
    edge.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(state.selectEdge).toHaveBeenCalledWith('edge-a');
  });

  it('loads an unloaded lazy subtree on its first expansion', () => {
    const state = fakeState();
    state.visibleNodes.set([{
      id: 'org-lazy', stable_key: 'org-lazy', kind: 'organization', label: 'Lazy Organization',
      parent_id: null, depth: 0, child_count: 2, has_more_children: true,
    }]);
    TestBed.configureTestingModule({
      imports: [OrganizationHierarchyComponent],
      providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
    });
    const fixture = TestBed.createComponent(OrganizationHierarchyComponent);
    fixture.detectChanges();
    const item = fixture.nativeElement.querySelector('[role="treeitem"]') as HTMLButtonElement;
    const disclosure = fixture.nativeElement.querySelector('.disclosure') as HTMLElement;

    expect(item.getAttribute('aria-expanded')).toBe('false');
    disclosure.click();

    expect(state.loadChildren).toHaveBeenCalledWith('org-lazy');
  });

  it('enforces the 500-node render budget without count-specific branches', () => {
    const state = fakeState();
    state.visibleNodes.set(Array.from({ length: 501 }, (_, index) => ({
      id: `node-${index}`, stable_key: `node-${index}`, kind: 'team' as const, label: `Team ${index}`,
      parent_id: null, depth: 0, child_count: 0,
    })));
    state.visibleEdges.set([]);
    TestBed.configureTestingModule({
      imports: [OrganizationGraphComponent],
      providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
    });
    const fixture = TestBed.createComponent(OrganizationGraphComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('.node').length).toBe(500);
    expect(fixture.nativeElement.textContent).toContain('Darstellung auf 500 Knoten');
  });

  it('fits and zooms through the domain-neutral viewport adapter', () => {
    const adapter = new DefaultCanvasViewportAdapter();
    const fitted = adapter.fit([{ x: 0, y: 0 }, { x: 400, y: 200 }], 1000, 600);
    const zoomed = adapter.zoomAt(fitted, { x: 500, y: 300 }, 2);

    expect(fitted.zoom).toBeGreaterThanOrEqual(.25);
    expect(zoomed.zoom).toBeLessThanOrEqual(2.5);
    expect(zoomed.pan).not.toEqual(fitted.pan);
  });
});
