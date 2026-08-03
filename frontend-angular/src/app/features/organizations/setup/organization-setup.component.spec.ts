import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { describe, expect, it, vi } from 'vitest';

import {
  OrganizationBlueprintSummary,
  OrganizationCompilePlan,
  OrganizationInstantiationGrant,
} from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationSetupComponent } from './organization-setup.component';

describe('OrganizationSetupComponent standard selector binding', () => {
  it('keeps blueprint and team count synchronized in both selection directions', () => {
    const { component, blueprints } = createComponent();
    const enterprise5 = blueprints.find(item => item.key === 'enterprise:standard:5')!;
    const enterprise8 = blueprints.find(item => item.key === 'enterprise:standard:8')!;
    const alternative5 = blueprints.find(item => item.key === 'alternative:standard:5')!;
    const alternative8 = blueprints.find(item => item.key === 'alternative:standard:8')!;

    expect(component.blueprintKey).toBe(enterprise8.key);
    expect(component.teamCount).toBe(8);

    component.selectBlueprint(enterprise5.key);
    expect(component.blueprintKey).toBe(enterprise5.key);
    expect(component.teamCount).toBe(5);

    component.selectForCount(8);
    expect(component.blueprintKey).toBe(enterprise8.key);
    expect(component.teamCount).toBe(8);

    component.selectBlueprint(alternative5.key);
    component.selectForCount(8);
    expect(component.blueprintKey).toBe(alternative8.key);
    expect(component.teamCount).toBe(8);
  });

  it('refuses mismatches and compiles only an atomically selected server summary', () => {
    const { component, compile } = createComponent();
    component.blueprintKey = 'enterprise:standard:5';
    component.teamCount = 8;

    component.compile();

    expect(component.canCompile()).toBe(false);
    expect(compile).not.toHaveBeenCalled();

    component.selectBlueprint('enterprise:standard:5');
    component.compile();

    expect(compile).toHaveBeenCalledWith({
      blueprint_key: 'enterprise:standard:5',
      title: 'Enterprise Produktorganisation',
      team_count: 5,
    });
    expect(component.teamCount).toBe(5);
  });

  it('binds the blueprint dropdown to the atomic selector handler', () => {
    const { component, fixture } = createComponent();
    const blueprintSelect = fixture.debugElement.query(By.css('select[name="blueprint"]'));

    blueprintSelect.triggerEventHandler('ngModelChange', 'enterprise:standard:5');
    fixture.detectChanges();

    expect(component.teamCount).toBe(5);
    const options = Array.from(
      fixture.nativeElement.querySelectorAll('select[name="blueprint"] option'),
    ) as HTMLOptionElement[];
    expect(options).toHaveLength(2);
    expect(options.every(option => option.textContent?.includes('5 Teams'))).toBe(true);
  });
});

describe('OrganizationSetupComponent instantiation grant flow', () => {
  it('does not expose a manual grant field and requests a bound grant', () => {
    const harness = createComponent();
    enterCompilePreview(harness);

    expect(harness.fixture.debugElement.query(By.css('input[type="password"]'))).toBeNull();
    expect(harness.fixture.nativeElement.textContent).not.toContain(
      'Gebundener Organization-Admin-Grant',
    );

    clickButton(harness.fixture, 'Freigabe anfordern');

    expect(harness.state.issueInstantiationGrant).toHaveBeenCalledOnce();
    expect(harness.state.issueInstantiationGrant).toHaveBeenCalledWith();
  });

  it('requires a valid receipt before confirmation can instantiate without arguments', () => {
    const harness = createComponent();
    const plan = enterCompilePreview(harness);

    expect(harness.component.step).toBe(2);
    expect(harness.fixture.nativeElement.textContent).not.toContain(
      'Bewusst instanziieren',
    );

    harness.state.instantiationGrant.set(instantiationGrant(plan));
    harness.fixture.detectChanges();

    expect(harness.component.step).toBe(3);
    expect(harness.fixture.nativeElement.textContent).toContain(
      'Bewusst instanziieren',
    );
    expect(harness.fixture.debugElement.query(By.css('input[type="password"]'))).toBeNull();
    expect(harness.fixture.nativeElement.textContent).not.toContain(
      'Gebundener Organization-Admin-Grant',
    );
    expect(harness.state.hasValidInstantiationGrant).toHaveReturnedWith(true);

    harness.component.confirmed = true;
    harness.fixture.changeDetectorRef.detectChanges();
    const instantiateButton = buttonByText(
      harness.fixture,
      'Organisation instanziieren',
    );
    expect(instantiateButton.disabled).toBe(false);
    instantiateButton.click();

    expect(harness.state.instantiate).toHaveBeenCalledOnce();
    expect(harness.state.instantiate).toHaveBeenCalledWith();
  });

  it('discards the bound compile plan through the state boundary', () => {
    const harness = createComponent();
    enterCompilePreview(harness);
    harness.state.discardCompilePlan.mockClear();

    clickButton(harness.fixture, 'Ändern');

    expect(harness.state.discardCompilePlan).toHaveBeenCalledOnce();
    expect(harness.state.discardCompilePlan).toHaveBeenCalledWith();
  });
});

function createComponent() {
  const blueprints = [
    blueprint('alternative', 5),
    blueprint('enterprise', 5),
    blueprint('alternative', 8),
    blueprint('enterprise', 8, true),
  ];
  const compile = vi.fn();
  const compilePlan = signal<OrganizationCompilePlan | null>(null);
  const instantiationGrant = signal<OrganizationInstantiationGrant | null>(null);
  const state = {
    projectId: signal('project-alpha'),
    blueprints: signal<readonly OrganizationBlueprintSummary[]>(blueprints),
    compilePlan,
    instantiationGrant,
    mutating: signal(false),
    instantiationPending: signal(false),
    instantiationOutcomeUncertain: signal(false),
    compile,
    compileCustom: vi.fn(),
    hasValidInstantiationGrant: vi.fn(() => {
      const plan = compilePlan();
      const grant = instantiationGrant();
      return Boolean(
        plan
        && grant
        && grant.grant_kind === 'instantiate'
        && grant.project_id === 'project-alpha'
        && grant.plan_digest === plan.plan_digest
        && grant.policy_hash === plan.admin_policy_hash
        && grant.expires_at > 2_000_000_000,
      );
    }),
    issueInstantiationGrant: vi.fn(),
    discardCompilePlan: vi.fn(() => {
      compilePlan.set(null);
      instantiationGrant.set(null);
    }),
    instantiate: vi.fn(),
  };
  TestBed.configureTestingModule({
    imports: [OrganizationSetupComponent],
    providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
  });
  const fixture = TestBed.createComponent(OrganizationSetupComponent);
  fixture.detectChanges();
  return {
    component: fixture.componentInstance,
    fixture,
    blueprints,
    compile,
    state,
  };
}

function enterCompilePreview(harness: ReturnType<typeof createComponent>): OrganizationCompilePlan {
  const plan = compilePlan();
  harness.state.compilePlan.set(plan);
  harness.fixture.detectChanges();
  return plan;
}

function clickButton(
  fixture: ComponentFixture<OrganizationSetupComponent>,
  label: string,
): void {
  buttonByText(fixture, label).click();
}

function buttonByText(
  fixture: ComponentFixture<OrganizationSetupComponent>,
  label: string,
): HTMLButtonElement {
  const button = Array.from(
    fixture.nativeElement.querySelectorAll('button'),
  ).find(candidate => candidate.textContent?.includes(label));
  expect(button, `button containing "${label}"`).toBeTruthy();
  return button as HTMLButtonElement;
}

function compilePlan(): OrganizationCompilePlan {
  return {
    blueprint_key: 'enterprise:standard:8',
    blueprint_version: '1',
    title: 'Enterprise Produktorganisation',
    organization_id: 'organization-alpha',
    definition_ref: 'enterprise@1',
    definition_revision: 'definition-revision-alpha',
    plan_digest: 'plan-digest-alpha',
    compile_token: 'compile-token-alpha',
    expires_at: '2033-05-18T03:33:20Z',
    admin_policy_hash: 'admin-policy-alpha',
    composition_mode: 'standard',
    team_count: 8,
    unit_count: 9,
    hierarchy_edge_count: 8,
    relation_edge_count: 4,
    role_slot_count: 16,
    planned_writes: ['organization', 'teams', 'role_slots'],
    capability_gaps: [],
    unfilled_required_slots: [],
    budget_assumptions: {},
    diagnostics: [],
    limits: {
      revision: 'limit-revision-alpha',
      policy_hash: 'limit-policy-alpha',
      max_teams: 10,
      max_units: 32,
      max_role_slots: 64,
      max_assignments: 128,
      max_relations: 128,
      max_patch_operations: 32,
      max_page_size: 100,
      max_depth: 8,
      max_render_nodes: 500,
      max_render_edges: 1_000,
    },
  };
}

function instantiationGrant(
  plan: OrganizationCompilePlan,
): OrganizationInstantiationGrant {
  return {
    grant_id: 'instantiation-grant-alpha',
    grant_kind: 'instantiate',
    tenant_id: 'tenant-alpha',
    project_id: 'project-alpha',
    principal_id: 'operator-alpha',
    plan_digest: plan.plan_digest,
    policy_hash: plan.admin_policy_hash,
    expires_at: 2_000_000_001,
    replayed: false,
  };
}

function blueprint(
  definitionKey: string,
  teamCount: number,
  recommended = false,
): OrganizationBlueprintSummary {
  return {
    key: `${definitionKey}:standard:${teamCount}`,
    definition_key: definitionKey,
    version: '1',
    title: `${definitionKey} · ${teamCount} Teams`,
    team_count: teamCount,
    standard: true,
    recommended,
    test_only: false,
    revision: `${definitionKey}-revision`,
    supported_team_counts: [5, 8],
    custom_team_count_min: 2,
    custom_team_count_max: 10,
    custom_team_blueprints: [],
  };
}
