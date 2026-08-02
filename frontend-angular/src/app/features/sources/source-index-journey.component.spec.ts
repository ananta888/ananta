import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { ProjectContextService } from '../../services/project-context.service';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceControlIndexAccessApiClient } from '../../services/source-control-index-access-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';
import { SourceDetailFacade } from './source-detail.facade';
import { SourceIndexJourneyComponent } from './source-index-journey.component';

describe('SourceIndexJourneyComponent', () => {
  const selectedProjectId = signal<string | null>('project-alpha');
  const selectedProject = () => ({ id: 'project-alpha', name: 'Alpha' });
  const detail = {
    runs: signal<readonly never[]>([]),
    loading: signal(false),
    mutationLoading: signal(false),
    indexProfiles: signal<readonly { profileId: string }[]>([]),
    sourceError: signal(null),
    lifecycleMessage: signal(''),
    can: vi.fn().mockReturnValue(false),
    load: vi.fn(),
    refresh: vi.fn(),
    scan: vi.fn(),
    startIndex: vi.fn(),
    activateIndex: vi.fn(),
    rollbackIndex: vi.fn(),
  };
  const api = {
    listConnections: vi.fn(),
    validateConnection: vi.fn(),
    createConnection: vi.fn(),
  };
  const governance = { gitAuthorizationHealth: vi.fn() };
  const indexAccess = {
    prepare: vi.fn(),
    grant: vi.fn(),
  };
  const catalog = {
    loadWorkspaces: vi.fn(),
    loadRemotes: vi.fn(),
    loadIndexProfiles: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedProjectId.set('project-alpha');
    detail.runs.set([]);
    detail.indexProfiles.set([]);
    detail.can.mockReturnValue(false);
    indexAccess.prepare.mockReturnValue(of(indexAccessPreparation()));
    indexAccess.grant.mockReturnValue(of(indexAccessResult()));
    api.listConnections.mockReturnValue(of({ items: [], next_cursor: null }));
    governance.gitAuthorizationHealth.mockReturnValue(of({
      status: 'unavailable',
      reason_code: 'provider_not_configured',
      connector_ready: { github_repository: false, generic_git: false },
    }));
    catalog.loadWorkspaces.mockReturnValue(of([]));
    catalog.loadRemotes.mockReturnValue(of([]));
    catalog.loadIndexProfiles.mockReturnValue(of([]));
    TestBed.configureTestingModule({
      providers: [
        { provide: ProjectContextService, useValue: { selectedProjectId, selectedProject } },
        { provide: SourceControlV1ApiClient, useValue: api },
        { provide: SourceControlV1GovernanceApiClient, useValue: governance },
        { provide: SourceControlIndexAccessApiClient, useValue: indexAccess },
        { provide: SourceConnectorCatalogService, useValue: catalog },
        { provide: SourceDetailFacade, useValue: detail },
      ],
    });
  });

  it('keeps public Git and workspace onboarding available while gating an unavailable private provider', () => {
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();

    expect(journey.providerAccess()).toEqual({
      publicGit: true,
      workspace: true,
      privateGit: false,
    });
    expect(journey.stage()).toBe('choose');
  });

  it('only selects a Hub-listed connection from the active project', () => {
    const connectionId = `conn_${'1'.repeat(64)}`;
    api.listConnections.mockReturnValue(of({
      items: [{
        connection_id: connectionId,
        connection: {
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'git',
        },
      }],
      next_cursor: null,
    }));
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();

    journey.chooseExisting(connectionId);

    expect(journey.selectedConnectionId()).toBe(connectionId);
    expect(journey.stage()).toBe('scan');
    expect(detail.load).toHaveBeenCalledWith(connectionId);
    journey.chooseExisting('browser-invented-connection');
    expect(detail.load).toHaveBeenCalledTimes(1);
  });

  it('starts indexing only after the detail facade has the same Hub profile and aggregate access grant', () => {
    const connectionId = `conn_${'2'.repeat(64)}`;
    const profile = {
      profileId: 'profile-deep-code',
      label: 'Deep Code',
      description: '',
      isDefault: true,
    };
    api.listConnections.mockReturnValue(of({
      items: [{
        connection_id: connectionId,
        connection: {
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'git',
        },
      }],
      next_cursor: null,
    }));
    catalog.loadIndexProfiles.mockReturnValue(of([profile]));
    detail.can.mockImplementation((action: string) => action === 'index' || action === 'grant');
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();
    journey.chooseExisting(connectionId);

    expect(journey.profileId()).toBe(profile.profileId);
    expect(journey.canStartIndex()).toBe(false);

    detail.indexProfiles.set([profile]);

    expect(journey.canStartIndex()).toBe(false);
    journey.prepareIndexAccess();
    journey.accessConfirmed.set(true);
    journey.grantIndexAccess();

    expect(journey.canStartIndex()).toBe(true);
    expect(indexAccess.grant).toHaveBeenCalledWith(
      expect.objectContaining({ connection_id: connectionId }),
      'project-alpha',
      expect.objectContaining({ confirmed: true }),
      expect.stringMatching(/^ui:index-access:/),
    );
  });

  it('keeps the aggregate command locked until the local redacted effect is explicitly confirmed', () => {
    const connectionId = `conn_${'5'.repeat(64)}`;
    api.listConnections.mockReturnValue(of({
      items: [{
        connection_id: connectionId,
        connection: {
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'git',
        },
      }],
      next_cursor: null,
    }));
    detail.can.mockImplementation((action: string) => action === 'index' || action === 'grant');
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();
    journey.chooseExisting(connectionId);

    journey.prepareIndexAccess();

    expect(journey.selectedAccessOption()?.effect).toEqual({
      provider_location: 'local', transformation: 'redacted', one_time: true,
    });
    expect(journey.canGrantIndexAccess()).toBe(false);
    journey.grantIndexAccess();
    expect(indexAccess.grant).not.toHaveBeenCalled();

    journey.accessConfirmed.set(true);
    expect(journey.canGrantIndexAccess()).toBe(true);
  });
});

function indexAccessPreparation() {
  return {
    connection_id: `conn_${'2'.repeat(64)}`,
    source_revision: {
      source_revision_id: `srev_${'3'.repeat(64)}`,
      revision_digest: '4'.repeat(64),
      admission_state: 'admitted',
      captured_at: '2026-08-01T12:00:00Z',
    },
    destinations: [{
      destination_id: 'worker-alpha',
      worker_id: 'worker-alpha',
      runtime_kind: 'codecompass',
      provider_location: 'local' as const,
      data_residency: 'local-container',
    }],
    options: [{
      option_id: 'redacted-local-once',
      preset_id: 'preset-redacted-index',
      label: 'Lokal redigiert',
      effect: { provider_location: 'local' as const, transformation: 'redacted' as const, one_time: true as const },
      duration_seconds: { minimum: 60, maximum: 900, default: 900 },
    }],
    readiness: { ready: true, reason_codes: [] },
    etag: 'a'.repeat(64),
  };
}

function indexAccessResult() {
  const preparation = indexAccessPreparation();
  return {
    access_ready: true as const,
    connection_id: preparation.connection_id,
    source_revision_id: preparation.source_revision.source_revision_id,
    destination_id: preparation.destinations[0].destination_id,
    option_id: preparation.options[0].option_id,
    effect: preparation.options[0].effect,
    policy: { policy_id: 'policy-index-access', version: 1, state: 'active', etag: 'b'.repeat(64) },
    grant: { grant_id: 'grant-index-access', state: 'active', etag: 'c'.repeat(64), expires_at: '2026-08-01T12:15:00Z' },
    next_actions: ['start_index_run'] as const,
  };
}
