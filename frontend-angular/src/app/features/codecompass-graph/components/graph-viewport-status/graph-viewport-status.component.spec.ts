import { ComponentFixture, TestBed } from '@angular/core/testing';

import { GraphViewportSummary } from '../../models/graph-viewport-summary.model';
import { GraphViewportStatusComponent } from './graph-viewport-status.component';

function summary(
  overrides: Partial<GraphViewportSummary> = {},
): Readonly<GraphViewportSummary> {
  return {
    nodes: { visible: 8, loaded: 20, total: 10_618 },
    edges: { visible: 7, loaded: 30, internalWindow: 42, total: 107_062 },
    domains: { visible: 2, loaded: 5 },
    rawRelationTypes: { visible: 3, loaded: 9 },
    nodeWindowBounded: true,
    edgeWindowCapped: true,
    semanticState: 'partial',
    artifactState: 'complete',
    projectionIssues: [
      { code: 'graph_node_window_bounded', affectedCount: 10_598, reasonCode: null },
      { code: 'graph_edge_window_capped', affectedCount: 12, reasonCode: null },
    ],
    semanticIssues: [
      { code: 'semantic_graph_truncated', affectedCount: 97_766, reasonCode: 'semantic_graph_partial' },
      { code: 'semantic_graph_candidate_relations_truncated', affectedCount: 5_000, reasonCode: 'semantic_graph_partial' },
      { code: 'semantic_graph_relations_unresolved', affectedCount: 20_000, reasonCode: 'semantic_graph_partial' },
    ],
    artifactIssues: [],
    rawWarnings: ['Raw worker warning'],
    ...overrides,
  };
}

describe('GraphViewportStatusComponent', () => {
  let fixture: ComponentFixture<GraphViewportStatusComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphViewportStatusComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphViewportStatusComponent);
  });

  it('separates total, loaded-window, visible and inventory statistics', () => {
    fixture.componentRef.setInput('summary', summary());
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-total"]')?.textContent)
      .toContain('10.618 Knoten');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-window"]')?.textContent)
      .toContain('30 Relationen');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-window"]')?.textContent)
      .toContain('von 42 im Knotenfenster');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-visible"]')?.textContent)
      .toContain('8 Knoten');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-inventory"]')?.textContent)
      .toContain('2/5 Domains');
  });

  it('shows structured semantic incompleteness prominently and leaves raw prose collapsed', () => {
    fixture.componentRef.setInput('summary', summary());
    fixture.detectChanges();

    const warning = fixture.nativeElement.querySelector('[data-testid="graph-semantic-warning"]');
    expect(warning.textContent).toContain('Semantischer Graph unvollständig');
    expect(warning.textContent).toContain('Davon liegen 5.000 Kandidatenrelationen');
    expect(warning.textContent).toContain('20.000 semantische Relationen');
    const details = fixture.nativeElement.querySelector('details');
    expect(details.open).toBe(false);
    expect(details.textContent).toContain('Raw worker warning');
  });

  it('renders missing totals as unknown without inventing completeness', () => {
    fixture.componentRef.setInput('summary', summary({
      nodes: { visible: 1, loaded: 1, total: null },
      edges: { visible: 0, loaded: 0, internalWindow: null, total: null },
      nodeWindowBounded: null,
      edgeWindowCapped: null,
      projectionIssues: [],
      semanticState: 'unknown',
      semanticIssues: [],
      rawWarnings: [],
    }));
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="graph-stats-total"]')?.textContent)
      .toContain('nicht gemeldet');
    expect(fixture.nativeElement.querySelector('[data-testid="graph-semantic-warning"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-testid="graph-window-warning"]')).toBeNull();
  });

  it('distinguishes an unverified semantic scope from an incomplete one', () => {
    fixture.componentRef.setInput('summary', summary({
      semanticState: 'unknown',
      semanticIssues: [{
        code: 'semantic_scope_unverified',
        affectedCount: null,
        reasonCode: null,
      }],
      artifactIssues: [],
    }));
    fixture.detectChanges();

    const warning = fixture.nativeElement.querySelector('[data-testid="graph-semantic-warning"]');
    expect(warning.textContent).toContain('Semantische Vollständigkeit nicht bestätigt');
    expect(warning.textContent).toContain('vollständig übertragen');
    expect(warning.textContent).toContain('nicht verifiziert');
  });
});
