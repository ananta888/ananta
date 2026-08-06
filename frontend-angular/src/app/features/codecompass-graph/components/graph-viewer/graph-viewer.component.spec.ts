import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { GraphViewerComponent } from './graph-viewer.component';
import { GraphStateService } from '../../services/graph-state.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { GraphVisualProfileFacade } from '../../services/graph-visual-profile.facade';
import { GraphToolbarComponent } from '../graph-toolbar/graph-toolbar.component';
import { GraphEdgeLegendComponent } from '../graph-legend/graph-edge-legend.component';
import { Graph3dViewComponent } from '../graph-3d-view/graph-3d-view.component';

describe('GraphViewerComponent', () => {
  let fixture: ComponentFixture<GraphViewerComponent>;
  let component: GraphViewerComponent;
  let state: GraphStateService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphViewerComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphViewerComponent);
    component = fixture.componentInstance;
    state = fixture.debugElement.injector.get(GraphStateService);
    fixture.detectChanges();
  });

  it('starts in simple view mode', () => {
    expect(state.viewMode()).toBe('simple');
  });

  it('populates graph state when rawGraphData is set', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    expect(state.graph()).not.toBeNull();
    expect(state.graph()!.nodes.length).toBe(20);
  });

  it('renders simple view by default', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    const simpleView = fixture.nativeElement.querySelector('app-simple-graph-view');
    expect(simpleView).toBeTruthy();
  });

  it('switches to 2d view on mode change', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.setViewMode('2d');
    fixture.detectChanges();
    const view2d = fixture.nativeElement.querySelector('app-graph-2d-view');
    expect(view2d).toBeTruthy();
  });

  it('renders 3d view component when mode is 3d', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.setViewMode('3d');
    fixture.detectChanges();
    const view3d = fixture.nativeElement.querySelector('app-graph-3d-view');
    expect(view3d).toBeTruthy();
  });

  it('switches the 3d layout without changing the filtered graph population', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.setViewMode('3d');
    state.updateFilter({ searchText: 'Order' });
    fixture.detectChanges();
    const toolbar = fixture.debugElement.query(By.directive(GraphToolbarComponent))
      .componentInstance as GraphToolbarComponent;
    const nodeIds = state.filteredNodes().map(node => node.id);
    const edgeIds = state.filteredEdges().map(edge => edge.id);

    toolbar.graph3dLayoutModeChange.emit('hierarchical');
    fixture.detectChanges();
    const view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;

    expect(view.layoutMode).toBe('hierarchical');
    expect(view.graph?.nodes.map(node => node.id)).toEqual(nodeIds);
    expect(view.graph?.edges.map(edge => edge.id)).toEqual(edgeIds);
    expect(view.renderGraph?.nodes).toHaveLength(nodeIds.length);
    expect(view.renderGraph?.edges).toHaveLength(edgeIds.length);
  });

  it('removes filtered nodes from the physical 3d simulation graph', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.setViewMode('3d');
    const hiddenDomain = state.domainInventory()[0];
    state.setDomainVisible(hiddenDomain, false);
    fixture.detectChanges();

    const view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;

    expect(view.graph?.nodes.length).toBe(state.filteredNodes().length);
    expect(view.graph?.edges.length).toBe(state.filteredEdges().length);
    expect(view.graph?.nodes.length).toBeLessThan(state.graph()!.nodes.length);
  });

  it('shows detail panel when a node is selected', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.selectNode(state.graph()!.nodes[0]);
    fixture.detectChanges();
    const panel = fixture.nativeElement.querySelector('app-graph-detail-panel');
    expect(panel).toBeTruthy();
  });

  it('hides detail panel when selection is cleared', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.selectNode(state.graph()!.nodes[0]);
    fixture.detectChanges();
    state.clearSelection();
    fixture.detectChanges();
    const panel = fixture.nativeElement.querySelector('app-graph-detail-panel');
    expect(panel).toBeNull();
  });

  it('keeps shared detail selection independent from local 3d focus clearing', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.selectNode(state.graph()!.nodes[0]);
    state.setViewMode('3d');
    fixture.detectChanges();
    const view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;

    view.selectionCleared.emit();

    expect(state.selectedNode()).not.toBeNull();
    expect(state.selectedEdge()).toBeNull();
  });

  it('wires the visible connection-depth control to the viewer-local state', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.selectNode(state.graph()!.nodes[0]);
    const toolbar = fixture.debugElement.query(By.directive(GraphToolbarComponent))
      .componentInstance as GraphToolbarComponent;

    toolbar.neighborhoodDepthChange.emit(2);
    fixture.detectChanges();

    expect(state.focusHopDepth()).toBe(2);
    expect(state.focusNodeId()).toBe(state.selectedNode()!.id);
  });

  it('shows structured total, window and semantic completeness evidence', () => {
    fixture.componentRef.setInput('rawGraphData', {
      ...MOCK_DOMAIN_GRAPH_ARTIFACT,
      metadata: {
        ...(MOCK_DOMAIN_GRAPH_ARTIFACT.metadata ?? {}),
        view: 'topology',
        total_nodes: 10_618,
        total_edges: 107_062,
        internal_edge_count: 42,
        edge_capped: true,
        max_edges: 30,
      },
      diagnostics: {
        semantic_translation: {
          status: 'degraded',
          reason: 'semantic_graph_partial',
          semantic_budget: {
            truncated: true,
            truncated_node_count: 97_766,
            unresolved_edge_count: 20_000,
          },
        },
      },
      artifact_status: { state: 'available', manifest_present: true },
      warnings: ['Indexer record budget reached.'],
    });
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-total"]')?.textContent)
      .toContain('10.618 Knoten');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-window"]')?.textContent)
      .toContain('20 Knoten');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-visible"]')?.textContent)
      .toContain('30 Relationen');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-semantic-warning"]')?.textContent)
      .toContain('Semantischer Graph unvollständig');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-window-warning"]')?.textContent)
      .toContain('Relationen im Knotenfenster wurden nicht mitgeladen');
  });

  it('updates current-view statistics after a client-local filter', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();

    state.updateFilter({ searchText: 'LegacyAdapter' });
    fixture.detectChanges();

    const visible = fixture.nativeElement.querySelector('[data-testid="graph-stats-visible"]');
    expect(visible.textContent).toContain('1 Knoten');
    expect(visible.textContent).toContain('0 Relationen');
    expect(component.domainFilterOptions()
      .reduce((sum, option) => sum + option.visibleNodeCount, 0)).toBe(1);
    expect(component.relationFilterOptions()
      .reduce((sum, option) => sum + option.visibleEdgeCount, 0)).toBe(0);
  });

  it('preserves viewer state and an active profile across windows of one logical graph', () => {
    const profiles = fixture.debugElement.injector.get(GraphVisualProfileFacade);
    const initialWindow = {
      ...MOCK_DOMAIN_GRAPH_ARTIFACT,
      metadata: {
        ...(MOCK_DOMAIN_GRAPH_ARTIFACT.metadata ?? {}),
        view: 'topology',
        graph_revision: 'projection-window-100',
        evidence_graph_revision: 'evidence-revision-1',
        domain_scope: 'source:orders',
        include_subdomains: true,
      },
    };
    fixture.componentRef.setInput('rawGraphData', initialWindow);
    fixture.detectChanges();
    const selectedId = state.graph()!.nodes[0].id;
    state.selectNode(state.graph()!.nodes[0]);
    state.setNeighborhoodDepth(2);
    state.updateFilter({ searchText: 'Order' });
    profiles.activate({
      ...profiles.activeProfile(),
      nodeSizeRange: { min: 9, max: 31 },
    });

    fixture.componentRef.setInput('rawGraphData', {
      ...initialWindow,
      metadata: {
        ...initialWindow.metadata,
        graph_revision: 'projection-window-200',
        window_node_limit: 200,
      },
    });
    fixture.detectChanges();

    expect(state.selectedNode()?.id).toBe(selectedId);
    expect(state.focusNodeId()).toBe(selectedId);
    expect(state.focusHopDepth()).toBe(2);
    expect(state.filter().searchText).toBe('Order');
    expect(profiles.activeProfile().nodeSizeRange).toEqual({ min: 9, max: 31 });
  });

  it('resets viewer-local intent when the server domain scope changes', () => {
    const artifact = {
      ...MOCK_DOMAIN_GRAPH_ARTIFACT,
      metadata: {
        ...(MOCK_DOMAIN_GRAPH_ARTIFACT.metadata ?? {}),
        view: 'topology',
        graph_revision: 'projection-1',
        evidence_graph_revision: 'evidence-revision-1',
        domain_scope: 'source:orders',
        include_subdomains: true,
      },
    };
    fixture.componentRef.setInput('rawGraphData', artifact);
    fixture.detectChanges();
    state.selectNode(state.graph()!.nodes[0]);
    state.setNeighborhoodDepth(2);
    state.updateFilter({ searchText: 'Order' });

    fixture.componentRef.setInput('rawGraphData', {
      ...artifact,
      metadata: { ...artifact.metadata, domain_scope: 'source:billing' },
    });
    fixture.detectChanges();

    expect(state.selectedNode()).toBeNull();
    expect(state.focusHopDepth()).toBe(0);
    expect(state.filter().searchText).toBe('');
  });

  it('preserves local 3D focus within a window context and resets it on evidence revision', () => {
    const artifact = {
      ...MOCK_DOMAIN_GRAPH_ARTIFACT,
      metadata: {
        ...(MOCK_DOMAIN_GRAPH_ARTIFACT.metadata ?? {}),
        view: 'topology',
        graph_revision: 'projection-100',
        evidence_graph_revision: 'evidence-1',
      },
    };
    fixture.componentRef.setInput('rawGraphData', artifact);
    state.setViewMode('3d');
    fixture.detectChanges();
    let view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;
    view.selectNode(state.graph()!.nodes[0].id);

    fixture.componentRef.setInput('rawGraphData', {
      ...artifact,
      metadata: { ...artifact.metadata, graph_revision: 'projection-200' },
    });
    fixture.detectChanges();
    view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;
    expect(view.focusedNodeId).toBe(state.graph()!.nodes[0].id);

    fixture.componentRef.setInput('rawGraphData', {
      ...artifact,
      metadata: {
        ...artifact.metadata,
        graph_revision: 'projection-new-evidence',
        evidence_graph_revision: 'evidence-2',
      },
    });
    fixture.detectChanges();
    view = fixture.debugElement.query(By.directive(Graph3dViewComponent))
      .componentInstance as Graph3dViewComponent;

    expect(view.focusedNodeId).toBeNull();
  });

  it('provides isolated graph and profile state for two viewers', () => {
    const second = TestBed.createComponent(GraphViewerComponent);
    second.detectChanges();
    const secondState = second.debugElement.injector.get(GraphStateService);
    const firstProfiles = fixture.debugElement.injector.get(GraphVisualProfileFacade);
    const secondProfiles = second.debugElement.injector.get(GraphVisualProfileFacade);
    expect(secondState).not.toBe(state);
    expect(secondProfiles).not.toBe(firstProfiles);
    state.updateFilter({ searchText: 'Order' });
    expect(secondState.filter().searchText).toBe('');
    const changed = {
      ...firstProfiles.activeProfile(),
      nodeSizeRange: { min: 8, max: 30 },
    };
    expect(firstProfiles.activate(changed).ok).toBe(true);
    expect(secondProfiles.activeProfile().nodeSizeRange).not.toEqual({ min: 8, max: 30 });
  });

  it('projects domain and relation legend data from the canonical unfiltered graph', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    expect(component.domainLegend().length).toBeGreaterThan(0);
    expect(component.edgeLegend().length).toBeGreaterThan(0);
    expect(component.edgeLegend().reduce((sum, entry) => sum + entry.totalEdges, 0)).toBe(30);
    expect(component.domainFilterOptions().every(option =>
      Number.isInteger(option.visibleNodeCount) && /^#[0-9A-F]{6}$/i.test(option.color),
    )).toBe(true);
    expect(component.relationFilterOptions().reduce((sum, option) => sum + option.edgeCount, 0))
      .toBe(30);
  });

  it('uses unique legend and settings aria-controls ids for multiple viewers', () => {
    const second = TestBed.createComponent(GraphViewerComponent);
    second.detectChanges();
    const firstIds = [...fixture.nativeElement.querySelectorAll('[aria-controls]')]
      .map((element: Element) => element.getAttribute('aria-controls'));
    const secondIds = [...second.nativeElement.querySelectorAll('[aria-controls]')]
      .map((element: Element) => element.getAttribute('aria-controls'));
    expect(firstIds.length).toBeGreaterThanOrEqual(3);
    expect(firstIds.some(id => secondIds.includes(id))).toBe(false);
  });

  it('does not advertise node size or edge thickness in simple capability mode', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    component.openDomainLegend(true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('app-graph-node-size-legend')).toBeNull();
    component.openEdgeLegend(true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('section[aria-label="Kantendicke"]')).toBeNull();
  });

  it('uses one relation-filter state for toolbar and legend toggles', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    const relation = component.edgeLegend()[0].rawEdgeType;
    const toolbar = fixture.debugElement.query(By.directive(GraphToolbarComponent)).componentInstance as GraphToolbarComponent;
    const legend = fixture.debugElement.query(By.directive(GraphEdgeLegendComponent)).componentInstance as GraphEdgeLegendComponent;

    legend.toggleRelation(relation, false);
    fixture.detectChanges();
    expect(toolbar.isEdgeChecked(relation)).toBe(false);

    toolbar.toggleEdge(relation, true);
    fixture.detectChanges();
    expect(component.edgeLegend().find(entry => entry.rawEdgeType === relation)?.visible).toBe(true);
  });
});
