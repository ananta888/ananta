import { TestBed } from '@angular/core/testing';

import { ModelAnalysisGraphComponent } from './model-analysis-graph.component';

describe('ModelAnalysisGraphComponent', () => {
  it('applies a client-side bound and exposes a textual graph alternative', () => {
    const fixture = TestBed.createComponent(ModelAnalysisGraphComponent);
    fixture.componentRef.setInput('graph', {
      schema: 'graph.v1',
      nodes: Array.from({ length: 205 }, (_, index) => ({
        node_id: `node-${index}`,
        label: `Layer ${index}`,
        kind: 'layer',
      })),
      edges: [],
      truncated: false,
    });
    fixture.detectChanges();

    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('[role="listitem"]').length).toBe(200);
    expect(root.textContent).toContain('Ansicht wurde auf 200 Knoten');
    expect(root.querySelector('table caption')?.textContent).toContain(
      'Gerichtete Kanten',
    );
  });
});
