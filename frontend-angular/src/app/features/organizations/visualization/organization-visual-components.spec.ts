import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { OrganizationGraphProjection } from './organization-graph-projection.service';
import { OrganizationGraphLegendComponent } from './organization-graph-legend.component';
import { OrganizationVisualProfileService } from './organization-visual-profile.service';
import { OrganizationVisualSettingsComponent } from './organization-visual-settings.component';

const PROJECTION: OrganizationGraphProjection = {
  graph: { nodes: [], edges: [] },
  nodeStyles: {},
  edgeStyles: {},
  nodeLegend: [],
  edgeLegend: [],
  sizeLegend: {
    metric: 'Explizite Wichtigkeit',
    references: [
      { label: 'klein', value: 5 },
      { label: 'mittel', value: 10 },
      { label: 'groß', value: 15 },
    ],
  },
  activeMetrics: {
    nodeColor: 'Explizite Führungsebene',
    edgeColor: 'Kantenart',
    edgeStrength: 'Runtime-Provenienz',
  },
  assignmentStatus: { observedRoleCount: 0, underAssignedRoleCount: 0, unknownRoleCount: 0 },
  roleTargets: [],
};

describe('organization 3d visual components', () => {
  it('renders every active visual metric in the legend', async () => {
    await TestBed.configureTestingModule({ imports: [OrganizationGraphLegendComponent] }).compileComponents();
    const fixture = TestBed.createComponent(OrganizationGraphLegendComponent);
    fixture.componentRef.setInput('projection', PROJECTION);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Knotenfarbe · Explizite Führungsebene');
    expect(text).toContain('Knotengröße · Explizite Wichtigkeit');
    expect(text).toContain('Kantenfarbe · Kantenart');
    expect(text).toContain('Kantenstärke · Runtime-Provenienz');
  });

  it('identifies repeated role targets by team and stable key', async () => {
    await TestBed.configureTestingModule({
      imports: [OrganizationVisualSettingsComponent],
      providers: [OrganizationVisualProfileService],
    }).compileComponents();
    const fixture = TestBed.createComponent(OrganizationVisualSettingsComponent);
    fixture.componentRef.setInput('roleTargets', [
      {
        key: 'delivery:002:product_lead',
        label: 'Product Lead',
        roleTemplateRef: 'scrum_product_owner@1',
        teamLabel: 'Delivery Cell 2',
      },
    ]);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Team: Delivery Cell 2');
    expect(text).toContain('Template: scrum_product_owner@1');
    expect(text).toContain('delivery:002:product_lead');
  });
});
