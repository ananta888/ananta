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

  it('keeps hop changes local until focus is applied when inactive', () => {
    fixture.componentRef.setInput('selectedNode', selectedNode);
    fixture.componentRef.setInput('focusActive', false);
    fixture.componentRef.setInput('focusHopDepth', 1);
    fixture.detectChanges();

    const emitted: number[] = [];
    component.focusRequested.subscribe(depth => emitted.push(depth));

    component.incHops();

    expect(component.localHops).toBe(2);
    expect(emitted).toEqual([]);
  });
});
