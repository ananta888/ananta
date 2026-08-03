import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ProjectContextService } from '../../../services/project-context.service';
import { OrganizationPage } from './organization-api.client';
import { OrganizationApiClient } from './organization-api.client';
import { OrganizationTopologyStateService } from './organization-topology-state.service';

describe('OrganizationTopologyStateService project scope', () => {
  it('reloads for a project change and ignores the cancelled previous response', () => {
    const selectedProjectId = signal('project-alpha');
    const alphaBlueprints = new Subject<OrganizationPage<any>>();
    const alphaOrganizations = new Subject<OrganizationPage<any>>();
    const betaBlueprints = new Subject<OrganizationPage<any>>();
    const betaOrganizations = new Subject<OrganizationPage<any>>();
    const api = {
      listBlueprints: vi.fn((_hubUrl: string, projectId: string) => (
        projectId === 'project-alpha' ? alphaBlueprints : betaBlueprints
      )),
      listOrganizations: vi.fn((_hubUrl: string, projectId: string) => (
        projectId === 'project-alpha' ? alphaOrganizations : betaOrganizations
      )),
      topology: vi.fn((_hubUrl: string, organizationId: string) => of(topology(organizationId))),
    };

    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();
    expect(api.listBlueprints).toHaveBeenCalledWith('https://hub.example', 'project-alpha');
    expect(api.listOrganizations).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      '',
      100,
    );

    selectedProjectId.set('project-beta');
    TestBed.flushEffects();
    expect(api.listBlueprints).toHaveBeenCalledWith('https://hub.example', 'project-beta');
    expect(state.organizations()).toEqual([]);

    betaBlueprints.next({ items: [blueprint('beta-blueprint')], next_cursor: null });
    betaBlueprints.complete();
    betaOrganizations.next({ items: [organization('beta-organization')], next_cursor: null });
    betaOrganizations.complete();

    alphaBlueprints.next({ items: [blueprint('alpha-blueprint')], next_cursor: null });
    alphaBlueprints.complete();
    alphaOrganizations.next({ items: [organization('alpha-organization')], next_cursor: null });
    alphaOrganizations.complete();

    expect(state.projectId()).toBe('project-beta');
    expect(state.blueprints().map(item => item.key)).toEqual(['beta-blueprint']);
    expect(state.organizations().map(item => item.id)).toEqual(['beta-organization']);
    expect(api.topology).toHaveBeenCalledWith(
      'https://hub.example',
      'beta-organization',
      expect.objectContaining({ include_runtime: true }),
    );
    expect(state.topology()?.organization_id).toBe('beta-organization');
  });

  it('fails locally before HTTP when no active project is selected', () => {
    const api = {
      listBlueprints: vi.fn(),
      listOrganizations: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        {
          provide: ProjectContextService,
          useValue: { selectedProjectId: signal('') },
        },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();

    expect(api.listBlueprints).not.toHaveBeenCalled();
    expect(state.errorReasonCode()).toBe('project_id_required');
    expect(state.error()).toContain('Projekt');
  });

  it('cancels an in-flight custom compile when the project changes', () => {
    const selectedProjectId = signal('project-alpha');
    const admission = new Subject<any>();
    const compile = new Subject<any>();
    const api = {
      listBlueprints: vi.fn(() => of({ items: [], next_cursor: null })),
      listOrganizations: vi.fn(() => of({ items: [], next_cursor: null })),
      issueAdmissionException: vi.fn(() => admission),
      compileBlueprint: vi.fn(() => compile),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.compileCustom(
      'enterprise-organization',
      '1.0.0',
      'Enterprise Organization',
      { delivery: 2 },
      'Targeted test composition',
    );
    admission.next({ status: 'issued', admission_exception_ref: 'admission-1' });
    expect(api.compileBlueprint).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      expect.objectContaining({ admission_exception_ref: 'admission-1' }),
    );

    selectedProjectId.set('project-beta');
    TestBed.flushEffects();
    compile.next({ organization_id: 'stale-alpha-organization' });

    expect(state.projectId()).toBe('project-beta');
    expect(state.compilePlan()).toBeNull();
  });

  it('invalidates topology and layout state when the organization changes', () => {
    const selectedProjectId = signal('project-alpha');
    const alphaTopology = new Subject<any>();
    const betaTopology = new Subject<any>();
    const api = {
      listBlueprints: vi.fn(() => of({ items: [], next_cursor: null })),
      listOrganizations: vi.fn(() => of({
        items: [organization('organization-alpha'), organization('organization-beta')],
        next_cursor: null,
      })),
      topology: vi.fn((_hubUrl: string, organizationId: string) => (
        organizationId === 'organization-alpha' ? alphaTopology : betaTopology
      )),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();
    state.updateLayout({ node_id: 'alpha-node', x: 12, y: 24 });
    state.selectOrganization('organization-beta');
    alphaTopology.next(topology('organization-alpha'));
    betaTopology.next(topology('organization-beta'));

    expect(state.selectedOrganizationId()).toBe('organization-beta');
    expect(state.topology()?.organization_id).toBe('organization-beta');
    expect(state.layoutPreferences().size).toBe(0);
  });
});

function blueprint(key: string): any {
  return {
    key,
    definition_key: key,
    version: '1.0.0',
    title: key,
    team_count: 8,
    standard: true,
    test_only: false,
    revision: 'revision-1',
    supported_team_counts: [8],
    custom_team_count_min: 2,
    custom_team_count_max: 10,
    custom_team_blueprints: [],
  };
}

function organization(id: string): any {
  return {
    id,
    key: id,
    title: id,
    lifecycle: 'active',
    definition_revision: 'revision-1',
    snapshot_hash: 'snapshot-1',
    team_count: 8,
    unit_count: 3,
    project_id: 'project-beta',
    lock_version: 1,
    revision: 'revision-1',
  };
}

function topology(organizationId: string): any {
  return {
    organization_id: organizationId,
    definition_revision: 'revision-1',
    snapshot_hash: 'snapshot-1',
    nodes: [],
    edges: [],
    runtime_overlay: null,
    diagnostics: [],
    limits: { max_page_size: 100 },
    next_cursor: null,
    truncated: false,
  };
}
