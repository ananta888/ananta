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

    const instantiateRequest = {
      compile_plan: { definition_revision: 'revision-1' } as OrganizationCompilePlan,
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
      ...instantiateRequest,
      project_id: 'project-alpha',
    });
    expect(instantiate.request.headers.get('If-Match')).toBe('"revision-1"');
    instantiate.flush(envelope({}));
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
