import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GraphToolbarComponent } from './graph-toolbar.component';
import { GraphViewMode } from '../../models/graph-view-mode';
import { GraphFilter } from '../../models/graph-filter.model';

describe('GraphToolbarComponent', () => {
  let fixture: ComponentFixture<GraphToolbarComponent>;
  let component: GraphToolbarComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphToolbarComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphToolbarComponent);
    component = fixture.componentInstance;
    component.filter = { searchText: '', nodeKindFilter: [], edgeTypeFilter: [] };
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
    component.filter = { searchText: 'x', nodeKindFilter: [], edgeTypeFilter: [] };
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
});
