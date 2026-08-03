import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { UserAuthService } from '../../../services/user-auth.service';
import {
  OrganizationCompilePlan,
  OrganizationInstantiateRequest,
} from '../models/organization-topology.models';
import { OrganizationApiClient } from './organization-api.client';

describe('OrganizationApiClient project scope', () => {
  let client: OrganizationApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        OrganizationApiClient,
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: UserAuthService, useValue: { token: null } },
      ],
    });
    client = TestBed.inject(OrganizationApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('binds catalog and organization reads to the selected project', () => {
    client.listBlueprints('https://hub.example', 'project-alpha').subscribe();
    const blueprints = http.expectOne(
      'https://hub.example/api/organization-blueprints?project_id=project-alpha&page_size=100',
    );
    expect(blueprints.request.method).toBe('GET');
    blueprints.flush(envelope({ items: [], next_cursor: null }));

    client.listOrganizations('https://hub.example', 'project-alpha', 'cursor-1', 100).subscribe();
    const organizations = http.expectOne(
      'https://hub.example/api/organizations?project_id=project-alpha&page_size=100&cursor=cursor-1',
    );
    expect(organizations.request.method).toBe('GET');
    organizations.flush(envelope({ items: [], next_cursor: null }));
  });

  it('binds compile, admission and instantiation bodies to the selected project', () => {
    client.compileBlueprint('https://hub.example', 'project-alpha', {
      blueprint_key: 'enterprise-organization-8',
      title: 'Enterprise Organization',
    }).subscribe();
    const compile = http.expectOne(
      'https://hub.example/api/organization-blueprints/enterprise-organization-8/compile',
    );
    expect(compile.request.body).toEqual({
      blueprint_key: 'enterprise-organization-8',
      title: 'Enterprise Organization',
      project_id: 'project-alpha',
    });
    compile.flush(envelope({}));

    client.issueAdmissionException(
      'https://hub.example',
      'project-alpha',
      'enterprise-organization',
      {
        team_blueprint_counts: { delivery: 2 },
        reason: 'Targeted test composition',
      },
      'organization-admission:test',
    ).subscribe();
    const admission = http.expectOne(
      'https://hub.example/api/organization-blueprints/enterprise-organization/admission-exceptions',
    );
    expect(admission.request.body).toEqual({
      team_blueprint_counts: { delivery: 2 },
      reason: 'Targeted test composition',
      project_id: 'project-alpha',
    });
    admission.flush(envelope({}));

    const plan = compilePlan();
    const instantiateRequest = {
      compile_plan: plan,
      title: 'Enterprise Organization',
      admin_grant: 'grant-1',
    } as OrganizationInstantiateRequest;
    client.instantiate(
      'https://hub.example',
      'project-alpha',
      instantiateRequest,
      'organization-instantiate:test',
    ).subscribe();
    const instantiate = http.expectOne('https://hub.example/api/organizations');
    expect(instantiate.request.body).toEqual({
      compile_plan: plan,
      title: 'Enterprise Organization',
      project_id: 'project-alpha',
    });
    expect(instantiate.request.headers.get('If-Match')).toBe('"revision-1"');
    expect(instantiate.request.headers.get('Idempotency-Key')).toBe('organization-instantiate:test');
    expect(instantiate.request.headers.get('X-Organization-Admin-Grant')).toBe('grant-1');
    expect(instantiate.request.headers.get('X-Plan-Digest')).toBe(plan.plan_digest);
    instantiate.flush(envelope({}));
  });

  it('issues a plan-bound instantiation grant with project, revision and retry bindings', () => {
    const plan = compilePlan();

    client.issueInstantiationGrant(
      'https://hub.example',
      'project-alpha',
      plan,
      'organization-instantiation-grant:test',
    ).subscribe();

    const issue = http.expectOne(
      'https://hub.example/api/organization-blueprints/enterprise_scrum_organization/precreation-admin-grants',
    );
    expect(issue.request.method).toBe('POST');
    expect(issue.request.body).toEqual({
      compile_plan: plan,
      project_id: 'project-alpha',
      ttl_seconds: 900,
    });
    expect(issue.request.headers.get('If-Match')).toBe('"revision-1"');
    expect(issue.request.headers.get('Idempotency-Key')).toBe(
      'organization-instantiation-grant:test',
    );
    expect(issue.request.headers.has('X-Organization-Admin-Grant')).toBe(false);
    issue.flush(envelope({
      grant_id: 'opgrant-precreation-1',
      grant_kind: 'instantiate',
      tenant_id: 'tenant-alpha',
      project_id: 'project-alpha',
      principal_id: 'principal-alpha',
      plan_digest: plan.plan_digest,
      policy_hash: plan.admin_policy_hash,
      expires_at: Math.floor(Date.now() / 1000) + 900,
      replayed: false,
    }));
  });

  it('uses the bundle preview snake-case project contract', () => {
    client.previewBundle(
      'https://hub.example',
      'project-alpha',
      { schema_version: '2.0' },
      'fail',
    ).subscribe();

    const preview = http.expectOne(
      'https://hub.example/api/organization-bundles/import-preview',
    );
    expect(preview.request.method).toBe('POST');
    expect(preview.request.body).toEqual({
      bundle: { schema_version: '2.0' },
      project_id: 'project-alpha',
      conflict_strategy: 'fail',
      assignment_rebindings: {},
      instance_admission_exception_refs: {},
    });
    preview.flush(envelope({}));
  });
});

function envelope(data: unknown): unknown {
  return { status: 'success', data };
}

function compilePlan(): OrganizationCompilePlan {
  return {
    blueprint_key: 'enterprise_scrum_organization',
    blueprint_version: '1',
    title: 'Enterprise Organization',
    organization_id: 'organization-candidate-1',
    definition_ref: 'enterprise_scrum_organization@1',
    definition_revision: 'revision-1',
    plan_digest: 'a'.repeat(64),
    compile_token: 'compile-token-1',
    expires_at: '2099-01-01T00:00:00Z',
    admin_policy_hash: 'b'.repeat(64),
    composition_mode: 'standard',
    team_count: 8,
    unit_count: 12,
    hierarchy_edge_count: 11,
    relation_edge_count: 7,
    role_slot_count: 16,
    planned_writes: ['organization_instance'],
    capability_gaps: [],
    unfilled_required_slots: [],
    budget_assumptions: {},
    diagnostics: [],
    limits: {
      revision: 'limits-1',
      policy_hash: 'c'.repeat(64),
      max_teams: 10,
      max_units: 20,
      max_role_slots: 100,
      max_assignments: 100,
      max_relations: 100,
      max_patch_operations: 50,
      max_page_size: 100,
      max_depth: 10,
      max_render_nodes: 500,
      max_render_edges: 1_000,
    },
  };
}
