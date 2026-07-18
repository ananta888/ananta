import { ComponentFixture, TestBed } from '@angular/core/testing';

import { GraphDomainLegendComponent } from './graph-domain-legend.component';
import { GraphDomainLegendEntry } from './graph-legend.models';

const ENTRIES: readonly GraphDomainLegendEntry[] = [
  {
    domainId: 'agent/routes', label: 'agent/routes', color: '#1d4ed8', marker: '●',
    totalNodes: 3, visibleNodes: 2, internalEdges: 1,
    outgoingExternalEdges: 2, incomingExternalEdges: 4, sumNodeScore: 1.75, visible: true,
  },
  {
    domainId: 'unassigned', label: 'Nicht zugeordnet', color: '#64748b', marker: '◆',
    totalNodes: 1, visibleNodes: 0, internalEdges: 0,
    outgoingExternalEdges: 0, incomingExternalEdges: 1, sumNodeScore: 0.25, visible: false,
  },
];

describe('GraphDomainLegendComponent', () => {
  let fixture: ComponentFixture<GraphDomainLegendComponent>;
  let component: GraphDomainLegendComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [GraphDomainLegendComponent] }).compileComponents();
    fixture = TestBed.createComponent(GraphDomainLegendComponent);
    component = fixture.componentInstance;
    component.entries = ENTRIES;
    component.open = true;
    fixture.detectChanges();
  });

  it('renders each canonical domain once, including hidden domains', () => {
    const items = fixture.nativeElement.querySelectorAll('.domain-entry');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('2/3');
    expect(items[0].textContent).toContain('1.750');
    expect(items[1].textContent).toContain('0/1');
  });

  it('emits hover without changing visibility', () => {
    const values: Array<string | null> = [];
    component.domainHovered.subscribe(value => values.push(value));
    const first = fixture.nativeElement.querySelector('.domain-entry') as HTMLElement;
    first.dispatchEvent(new MouseEvent('mouseenter'));
    first.dispatchEvent(new MouseEvent('mouseleave'));
    expect(values).toEqual(['agent/routes', null]);
  });

  it('can re-enable a domain whose visible count is zero', () => {
    let change: { id: string; visible: boolean } | undefined;
    component.domainVisibilityChange.subscribe(value => (change = value));
    const boxes = fixture.nativeElement.querySelectorAll('input[type=checkbox]') as NodeListOf<HTMLInputElement>;
    boxes[1].checked = true;
    boxes[1].dispatchEvent(new Event('change'));
    expect(change).toEqual({ id: 'unassigned', visible: true });
  });

  it('provides accessible controls and restores focus when closed', async () => {
    const close = fixture.nativeElement.querySelector('.icon-button') as HTMLButtonElement;
    const trigger = fixture.nativeElement.querySelector('.legend-trigger') as HTMLButtonElement;
    expect(close.getAttribute('aria-label')).toBe('Domain-Legende schließen');
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    close.click();
    fixture.detectChanges();
    await Promise.resolve();
    expect(document.activeElement).toBe(trigger);
  });
});
