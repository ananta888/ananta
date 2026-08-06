import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GraphToolbarComponent } from './graph-toolbar.component';
import { GraphViewMode } from '../../models/graph-view-mode';
import { EMPTY_FILTER, GraphFilter } from '../../models/graph-filter.model';

describe('GraphToolbarComponent', () => {
  let fixture: ComponentFixture<GraphToolbarComponent>;
  let component: GraphToolbarComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphToolbarComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphToolbarComponent);
    component = fixture.componentInstance;
    component.filter = EMPTY_FILTER;
    fixture.detectChanges();
  });

  it('renders three mode buttons', () => {
    const btns = fixture.nativeElement.querySelectorAll('.mode-btn');
    expect(btns.length).toBe(3);
  });

  it('marks active mode button', () => {
    component.activeMode = '2d';
    fixture.detectChanges();
    const active = fixture.nativeElement.querySelectorAll('.mode-btn.active');
    expect(active.length).toBe(1);
    expect(active[0].textContent.trim()).toContain('2D');
  });

  it('emits viewModeChange when mode button clicked', () => {
    let emitted: GraphViewMode | null = null;
    component.viewModeChange.subscribe((m: GraphViewMode) => (emitted = m));
    const btns: NodeListOf<HTMLButtonElement> = fixture.nativeElement.querySelectorAll('.mode-btn');
    btns[1].click();
    expect(emitted).toBe('2d');
  });

  it('shows and emits 2d layout mode changes only in 2d mode', async () => {
    expect(fixture.nativeElement.querySelector('.layout-select')).toBeNull();

    component.activeMode = '2d';
    fixture.detectChanges();

    let emitted: string | null = null;
    component.layoutModeChange.subscribe(mode => (emitted = mode));
    const select = fixture.nativeElement.querySelector('.layout-select') as HTMLSelectElement;
    expect(select).toBeTruthy();
    select.value = 'domain';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(emitted).toBe('domain');
  });

  it('offers force, hierarchical and radial layouts only in 3d mode', async () => {
    expect(fixture.nativeElement.querySelector('[data-testid="graph-3d-layout"]')).toBeNull();
    component.activeMode = '3d';
    fixture.detectChanges();

    let emitted: string | null = null;
    component.graph3dLayoutModeChange.subscribe(mode => (emitted = mode));
    const select = fixture.nativeElement.querySelector(
      '[data-testid="graph-3d-layout"]',
    ) as HTMLSelectElement;
    const label = fixture.nativeElement.querySelector(
      `label[for="${select.id}"]`,
    ) as HTMLLabelElement;
    expect(label?.textContent).toContain('3D-Layout');
    expect([...select.options].map(option => option.value)).toEqual([
      'force', 'hierarchical', 'radial',
    ]);
    select.value = 'radial';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(emitted).toBe('radial');
  });

  it('emits filterChange with searchText on input', async () => {
    let emitted: Partial<GraphFilter> | null = null;
    component.filterChange.subscribe((f: Partial<GraphFilter>) => (emitted = f));
    const input = fixture.nativeElement.querySelector('.search-input') as HTMLInputElement;
    input.value = 'Order';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    await fixture.whenStable();
    expect(emitted?.searchText).toBe('Order');
  });

  it('emits filterReset when clear button present and clicked', () => {
    component.filter = { ...EMPTY_FILTER, searchText: 'x' };
    fixture.detectChanges();
    let emitted = false;
    component.filterReset.subscribe(() => (emitted = true));
    const btn = fixture.nativeElement.querySelector('.reset-btn') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    btn.click();
    expect(emitted).toBe(true);
  });

  it('does not show reset button when filter is empty', () => {
    const btn = fixture.nativeElement.querySelector('.reset-btn');
    expect(btn).toBeNull();
  });

  it('renders and filters an unknown raw relation from canonical inventory', () => {
    component.edgeTypes = ['parent_child', 'custom:unsafe<relation>'];
    component.edgeOpen.set(true);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('custom:unsafe<relation>');
    let emitted: Partial<GraphFilter> | null = null;
    component.filterChange.subscribe(value => (emitted = value));
    component.toggleEdge('custom:unsafe<relation>', false);
    expect(emitted?.edgeTypes).toEqual({ mode: 'subset', values: ['parent_child'] });
  });

  it('uses explicit none mode when every edge is disabled', () => {
    let emitted: Partial<GraphFilter> | null = null;
    component.filterChange.subscribe(value => (emitted = value));
    component.setAllEdges(false);
    expect(emitted?.edgeTypes).toEqual({ mode: 'none', values: [] });
  });

  it('keeps the loaded-window domain filter accessible independently of visual legends', () => {
    component.domainOptions = [
      { domainId: 'orders', label: 'Orders', nodeCount: 7, visibleNodeCount: 7, color: '#112233' },
      { domainId: 'billing', label: 'Billing', nodeCount: 4, visibleNodeCount: 2, color: '#445566' },
    ];
    component.domainOpen.set(true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector('[data-testid="graph-domain-filter"]');
    expect(button).toBeTruthy();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(button.getAttribute('aria-controls')).toBe(component.domainPanelId);
    expect(fixture.nativeElement.querySelector(`#${component.domainPanelId}`)).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('Domains im Ausschnitt');
    expect(fixture.nativeElement.textContent).toContain('Orders');
    expect(fixture.nativeElement.textContent).toContain('Billing');
    expect(fixture.nativeElement.textContent).toContain('2/4');

    let emitted: Partial<GraphFilter> | null = null;
    component.filterChange.subscribe(value => (emitted = value));
    component.toggleDomain('billing', false);
    expect(emitted?.domains).toEqual({ mode: 'subset', values: ['orders'] });
  });

  it('emits a bounded connection depth and explains a missing anchor', () => {
    component.neighborhoodDepth = 2;
    component.neighborhoodAnchorLabel = '';
    fixture.detectChanges();
    let emitted: number | null = null;
    component.neighborhoodDepthChange.subscribe(value => (emitted = value));

    component.changeNeighborhoodDepth(9);

    expect(emitted).toBe(6);
    expect(fixture.nativeElement.querySelector('[data-testid="graph-neighbourhood-status"]')
      .textContent).toContain('Knoten im Graphen auswählen');
    expect(fixture.nativeElement.textContent).toContain('1 Hop = eine sichtbare Relation');
    expect(fixture.nativeElement.textContent).toContain('Such-, Knoten-, Domain- und Relationsfilter');
  });

  it('shows labelled relation types with visible/window counts and supports local search', () => {
    component.relationOptions = [
      {
        relationType: 'contains_file', label: 'enthält Datei', edgeCount: 12,
        visibleEdgeCount: 5, color: '#22C55E', semanticState: 'known',
      },
      {
        relationType: 'vendor_link', label: 'Vendor link', edgeCount: 3,
        visibleEdgeCount: 0, color: '#64748B', semanticState: 'semantically_unknown',
      },
    ];
    component.edgeOpen.set(true);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Relationstypen im Ausschnitt');
    expect(fixture.nativeElement.textContent).toContain('enthält Datei');
    expect(fixture.nativeElement.textContent).toContain('5/12');
    expect(fixture.nativeElement.textContent).toContain('roh');

    component.edgeQuery.set('vendor');
    fixture.detectChanges();
    expect(component.matchingEdgeTypes()).toEqual(['vendor_link']);
    expect(fixture.nativeElement.textContent).not.toContain('enthält Datei');
  });

  it('shows the active anchor and current visible counts in the depth status', () => {
    component.neighborhoodDepth = 2;
    component.neighborhoodAnchorLabel = 'Repository';
    component.visibleNodeCount = 17;
    component.visibleEdgeCount = 21;
    fixture.detectChanges();

    const status = fixture.nativeElement.querySelector('[data-testid="graph-neighbourhood-status"]');
    expect(status.textContent).toContain('Fokus: Repository');
    expect(status.textContent).toContain('17 Knoten / 21 Relationen');
    expect(status.getAttribute('aria-live')).toBe('polite');
  });

  it('applies a relation group toggle to the complete group while a search is active', () => {
    component.relationOptions = [
      {
        relationType: 'contains_directory', label: 'enthält Ordner', edgeCount: 4,
        visibleEdgeCount: 4, color: '#0D9488', semanticState: 'known',
      },
      {
        relationType: 'contains_file', label: 'enthält Datei', edgeCount: 12,
        visibleEdgeCount: 12, color: '#14B8A6', semanticState: 'known',
      },
    ];
    component.edgeQuery.set('Ordner');
    const emitted: Partial<GraphFilter>[] = [];
    component.filterChange.subscribe(value => emitted.push(value));

    component.toggleEdgeGroup(component.edgeGroups[2], false);

    expect(emitted.at(-1)?.edgeTypes).toEqual({ mode: 'none', values: [] });
  });
});
