import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GraphDetailPanelComponent } from './graph-detail-panel.component';
import { GraphNode } from '../../models/graph.model';

describe('GraphDetailPanelComponent', () => {
  let fixture: ComponentFixture<GraphDetailPanelComponent>;
  let component: GraphDetailPanelComponent;

  const selectedNode: GraphNode = {
    id: 'node-a',
    kind: 'java_type',
    label: 'Node A',
    file: 'src/NodeA.java',
    content: '',
    recordId: 'record-a',
    metadata: {},
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphDetailPanelComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(GraphDetailPanelComponent);
    component = fixture.componentInstance;
  });

  it('emits the updated hop depth when focus is already active', () => {
    fixture.componentRef.setInput('selectedNode', selectedNode);
    fixture.componentRef.setInput('focusActive', true);
    fixture.componentRef.setInput('focusHopDepth', 1);
    fixture.detectChanges();

    const emitted: number[] = [];
    component.focusRequested.subscribe(depth => emitted.push(depth));

    component.incHops();

    expect(emitted).toEqual([2]);
  });

  it('emits every depth change immediately instead of staging a second local value', () => {
    fixture.componentRef.setInput('selectedNode', selectedNode);
    fixture.componentRef.setInput('focusActive', false);
    fixture.componentRef.setInput('focusHopDepth', 1);
    fixture.detectChanges();

    const emitted: number[] = [];
    component.focusRequested.subscribe(depth => emitted.push(depth));

    component.incHops();

    expect(emitted).toEqual([2]);
  });

  it('allows hop depth 0 and emits it when focus is active', () => {
    fixture.componentRef.setInput('selectedNode', selectedNode);
    fixture.componentRef.setInput('focusActive', true);
    fixture.componentRef.setInput('focusHopDepth', 1);
    fixture.detectChanges();

    const emitted: number[] = [];
    component.focusRequested.subscribe(depth => emitted.push(depth));

    component.decHops();

    expect(emitted).toEqual([0]);
  });

  it('defines hops in visible-relation terms and states the filter order', () => {
    fixture.componentRef.setInput('selectedNode', selectedNode);
    fixture.componentRef.setInput('focusHopDepth', 1);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Umgebung um diesen Knoten');
    expect(fixture.nativeElement.textContent).toContain('1 Hop = eine sichtbare Relation');
    expect(fixture.nativeElement.textContent).toContain('Such-, Knoten-, Domain- und Relationsfilter');
    expect(fixture.nativeElement.textContent).toContain('gesamte gefilterte Ausschnitt');
  });
});
