import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GraphViewerComponent } from './graph-viewer.component';
import { GraphStateService } from '../../services/graph-state.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';

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
    state = TestBed.inject(GraphStateService);
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

  it('shows 3d placeholder when mode is 3d', () => {
    fixture.componentRef.setInput('rawGraphData', MOCK_DOMAIN_GRAPH_ARTIFACT);
    fixture.detectChanges();
    state.setViewMode('3d');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('3D renderer not yet available');
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
});
