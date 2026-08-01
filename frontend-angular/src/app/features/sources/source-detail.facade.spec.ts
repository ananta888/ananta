import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { ProjectContextService } from '../../services/project-context.service';
import { SourceDetailFacade } from './source-detail.facade';

describe('SourceDetailFacade', () => {
  const connectionId = `conn_${'1'.repeat(64)}`;
  const revisionId = `srev_${'2'.repeat(64)}`;
  const indexId = `idx_${'3'.repeat(64)}`;
  const grantId = `grant_${'4'.repeat(64)}`;
  const connectionEtag = '9'.repeat(64);
  const indexEtag = '8'.repeat(64);
  const grantEtag = '7'.repeat(64);
  const policyEtag = '6'.repeat(64);
  const selectedProjectId = signal<string | null>('project-alpha');

  const getConnection = vi.fn();
  const listRuns = vi.fn();
  const startIndexRun = vi.fn();
  const activateIndex = vi.fn();
  const rollbackIndex = vi.fn();
  const core = {
    getConnection,
    listRuns,
    startIndexRun,
    activateIndex,
    rollbackIndex,
    loadGraph: vi.fn(),
    listEvents: vi.fn(),
    refreshConnection: vi.fn(),
    scanConnection: vi.fn(),
    disableConnection: vi.fn(),
  };

  const listIndexProfiles = vi.fn();
  const listGrantPresets = vi.fn();
  const listGrants = vi.fn();
  const createGrant = vi.fn();
  const revokeGrant = vi.fn();
  const governance = {
    listIndexProfiles,
    listGrantPresets,
    listGrants,
    createGrant,
    revokeGrant,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedProjectId.set('project-alpha');
    getConnection.mockReturnValue(of({
      etag: connectionEtag,
      projection: {
        schema: 'ananta.source-control.projection.v1',
        connection_id: connectionId,
        etag: connectionEtag,
        connection: {
          connection_id: connectionId,
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'direct_text',
          state: 'ready',
        },
        revision: {
          source_revision_id: revisionId,
          revision_digest: 'a'.repeat(64),
          captured_at: '2026-07-30T10:00:00Z',
        },
        admission: { state: 'admitted' },
        index: null,
        active_index: null,
        grants: [{ grant_id: 'legacy-projection-grant-must-not-win' }],
        health: {},
        next_actions: ['index', 'activate', 'rollback'],
        stale: false,
      },
    }));
    listRuns.mockReturnValue(of({
      items: [{
        knowledge_index_id: indexId,
        source_revision_id: revisionId,
        status: 'ready',
        etag: indexEtag,
        coverage: { percent: 100 },
        created_at: '2026-07-30T10:01:00Z',
        updated_at: '2026-07-30T10:02:00Z',
      }],
      active: null,
      next_cursor: null,
    }));
    startIndexRun.mockReturnValue(of({ accepted: true }));
    activateIndex.mockReturnValue(of({ accepted: true }));
    rollbackIndex.mockReturnValue(of({ accepted: true }));

    listIndexProfiles.mockReturnValue(of({
      items: [{
        profile_id: 'profile-default',
        label: 'Default',
        description: 'Default profile',
        is_default: true,
        capabilities: {},
      }],
      next_cursor: null,
      capabilities: {},
    }));
    listGrantPresets.mockReturnValue(of({
      items: [{
        schema: 'ananta.source-control.grant-preset.v1',
        preset_id: 'preset-read',
        label: 'Read',
        description: 'Read-only context',
        operation: 'read',
        transformation: 'none',
        purpose: 'analysis',
        max_duration_seconds: 1800,
      }],
      next_cursor: null,
      capabilities: {},
    }));
    listGrants.mockReturnValue(of({
      schema: 'ananta.source-control.grant-admin-list.v1',
      items: [{
        schema: 'ananta.source-control.grant-admin-item.v1',
        grant_id: grantId,
        grant_family_id: `grantfam_${'5'.repeat(64)}`,
        version: 1,
        source_revision_id: revisionId,
        destination_id: 'hub-destination-primary',
        preset_id: 'preset-read',
        operation: 'read',
        transformation: 'none',
        purpose: 'analysis',
        policy_version: 'policy-primary:7',
        state: 'active',
        issued_at: '2026-07-30T10:00:00Z',
        expires_at: '2026-07-30T10:30:00Z',
        expired: false,
        etag: grantEtag,
      }],
      next_cursor: null,
      capabilities: {},
    }));
    createGrant.mockReturnValue(of({}));
    revokeGrant.mockReturnValue(of({}));

    TestBed.configureTestingModule({
      providers: [
        SourceDetailFacade,
        { provide: SourceControlV1ApiClient, useValue: core },
        { provide: SourceControlV1GovernanceApiClient, useValue: governance },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
  });

  it('loads catalogs and grants from the project and revision delivered by the Hub projection', () => {
    const facade = TestBed.inject(SourceDetailFacade);

    facade.load(connectionId);

    expect(listIndexProfiles).toHaveBeenCalledWith('project-alpha');
    expect(listGrantPresets).toHaveBeenCalledWith('project-alpha');
    expect(listGrants).toHaveBeenCalledWith('project-alpha');
    expect(facade.grants()[0]?.grantId).toBe(grantId);
    expect(facade.grants().some((grant) => grant.grantId.includes('legacy'))).toBeFalsy();
    expect(facade.runs()[0]?.etag).toBe(indexEtag);
  });

  it('uses the connection ETag for runs and the active-pointer CAS for lifecycle changes', () => {
    const facade = TestBed.inject(SourceDetailFacade);
    facade.load(connectionId);

    facade.startIndex('profile-default');
    expect(startIndexRun).toHaveBeenCalledWith(
      connectionId,
      'profile-default',
      {
        etag: connectionEtag,
        idempotencyKey: expect.stringMatching(/^ui:index:start:/),
      },
    );

    facade.activateIndex(indexId);
    expect(activateIndex).toHaveBeenCalledWith(indexId, {
      etag: 'active:0',
      idempotencyKey: expect.stringMatching(/^ui:index:activate:/),
    });

    facade.rollbackIndex(indexId);
    expect(rollbackIndex).toHaveBeenCalledWith(indexId, {
      etag: 'active:0',
      idempotencyKey: expect.stringMatching(/^ui:index:rollback:/),
    });
  });

  it('creates and revokes grants with policy/grant CAS and no browser-invented source IDs', () => {
    const facade = TestBed.inject(SourceDetailFacade);
    facade.load(connectionId);

    facade.createGrant({
      destinationId: 'hub-destination-primary',
      policyId: 'policy-primary',
      presetId: 'preset-read',
      durationSeconds: 900,
      policyEtag,
    });

    expect(createGrant).toHaveBeenCalledWith(
      'project-alpha',
      {
        source_revision_id: revisionId,
        destination_id: 'hub-destination-primary',
        policy_id: 'policy-primary',
        preset_id: 'preset-read',
        duration_seconds: 900,
      },
      {
        etag: policyEtag,
        idempotencyKey: expect.stringMatching(/^ui:grant:create:/),
      },
    );

    facade.revokeGrant(grantId);
    expect(revokeGrant).toHaveBeenCalledWith(
      'project-alpha',
      grantId,
      'operator_revoked',
      {
        etag: grantEtag,
        idempotencyKey: expect.stringMatching(/^ui:grant:revoke:/),
      },
    );
  });

  it('rejects profiles and grants that were not returned by the server', () => {
    const facade = TestBed.inject(SourceDetailFacade);
    facade.load(connectionId);

    facade.startIndex('browser-invented-profile');
    facade.activateIndex(`idx_${'9'.repeat(64)}`);
    facade.createGrant({
      destinationId: 'hub-destination-primary',
      policyId: 'policy-primary',
      presetId: 'browser-invented-preset',
      durationSeconds: 900,
      policyEtag,
    });

    expect(startIndexRun).not.toHaveBeenCalled();
    expect(activateIndex).not.toHaveBeenCalled();
    expect(createGrant).not.toHaveBeenCalled();
  });

  it('discards a projection outside the globally selected project scope', () => {
    selectedProjectId.set('project-beta');
    const facade = TestBed.inject(SourceDetailFacade);

    facade.load(connectionId);

    expect(facade.source()).toBeNull();
    expect(facade.runs()).toEqual([]);
    expect(facade.sourceError()?.state).toBe('forbidden');
    expect(listIndexProfiles).not.toHaveBeenCalled();
    facade.startIndex('profile-default');
    expect(startIndexRun).not.toHaveBeenCalled();
  });
});
