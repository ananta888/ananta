import { ComponentFixture, TestBed } from '@angular/core/testing';

import { GraphEdgeLegendComponent } from './graph-edge-legend.component';
import { GraphEdgeLegendEntry } from './graph-legend.models';

const ENTRIES: readonly GraphEdgeLegendEntry[] = [
  {
    rawEdgeType: 'imports_symbol', label: 'Importiert Symbol', color: '#0891b2', marker: '→',
    semanticState: 'known', totalEdges: 4, visibleEdges: 3, multiplicitySum: 7, visible: true,
  },
  {
    rawEdgeType: '<script>alert(1)</script>', label: '<script>alert(1)</script>', color: '#94a3b8', marker: '⋯',
    semanticState: 'semantically_unknown', totalEdges: 1, visibleEdges: 0, multiplicitySum: 1, visible: false,
  },
];

describe('GraphEdgeLegendComponent', () => {
  let fixture: ComponentFixture<GraphEdgeLegendComponent>;
  let component: GraphEdgeLegendComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [GraphEdgeLegendComponent] }).compileComponents();
    fixture = TestBed.createComponent(GraphEdgeLegendComponent);
    component = fixture.componentInstance;
    component.entries = ENTRIES;
    component.open = true;
    fixture.detectChanges();
  });

  it('renders known and unknown raw relations once with total, visible, and multiplicity counts', () => {
    const items = fixture.nativeElement.querySelectorAll('.edge-entry');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('3/4');
    expect(items[0].textContent).toContain('7');
    expect(items[1].textContent).toContain('Semantik unbekannt');
  });

  it('renders an untrusted raw relation as text, never markup', () => {
    expect(fixture.nativeElement.querySelector('script')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('<script>alert(1)</script>');
  });

  it('emits the existing relation filter intent for a hidden relation', () => {
    let change: { id: string; visible: boolean } | undefined;
    component.relationVisibilityChange.subscribe(value => (change = value));
    const boxes = fixture.nativeElement.querySelectorAll('input[type=checkbox]') as NodeListOf<HTMLInputElement>;
    boxes[1].checked = true;
    boxes[1].dispatchEvent(new Event('change'));
    expect(change).toEqual({ id: '<script>alert(1)</script>', visible: true });
  });

  it('emits temporary hover separately from persistent visibility', () => {
    const hover: Array<string | null> = [];
    const toggles: unknown[] = [];
    component.relationHovered.subscribe(value => hover.push(value));
    component.relationVisibilityChange.subscribe(value => toggles.push(value));
    const first = fixture.nativeElement.querySelector('.edge-entry') as HTMLElement;
    first.dispatchEvent(new MouseEvent('mouseenter'));
    first.dispatchEvent(new MouseEvent('mouseleave'));
    expect(hover).toEqual(['imports_symbol', null]);
    expect(toggles).toEqual([]);
  });
});
