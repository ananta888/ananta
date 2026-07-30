import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceDetailFacade } from './source-detail.facade';

describe('SourceDetailFacade', () => {
  const connectionId = `conn_${'1'.repeat(64)}`;
  const revisionId = `srev_${'2'.repeat(64)}`;
  const indexId = `idx_${'3'.repeat(64)}`;
  const grantId = `grant_${'4'.repeat(64)}`;

  const core = jasmine.createSpyObj<SourceControlV1ApiClient>('SourceControlV1ApiClient', [
    'getConnection',
    'listRuns',
    'startIndexRun',
    'activateIndex',
    'rollbackIndex',
    'loadGraph',
    'listEvents',
    'refreshConnection',
    'scanConnection',
    'disableConnection',
  ]);
  const governance = jasmine.createSpyObj<SourceControlV1GovernanceApiClient>(
    'SourceControlV1GovernanceApiClient',
    [
      'listIndexProfiles',
      'listGrantPresets',
      'listGrants',
      'createGrant',
      'revokeGrant',
    ],
  );

  beforeEach(() => {
    Object.values(core).forEach((candidate) => {
      if (candidate && typeof candidate === 'function' && 'calls' in candidate) {
        (candidate as jasmine.Spy).calls.reset();
      }
    });
    Object.values(governance).forEach((candidate) => {
      if (candidate && typeof candidate === 'function' && 'calls' in candidate) {
        (candidate as jasmine.Spy).calls.reset();
      }
    });

    core.getConnection.and.returnValue(
      of({
        etag: '"connection-etag"',
        projection: {
          connection_id: connectionId,
          connection: {
            connection_id: connectionId,
            project_id: 'project-alpha',
            display_name: 'Primary source',
            connector_type: 'direct_text',
            state: 'ready',
          },
          revision: {
            source_revision_id: revisionId,
            revision_digest: `sha256:${'a'.repeat(64)}`,
            captured_at: '2026-07-30T10:00:00Z',
          },
          admission: { state: 'admitted' },
          grants: [{ grant_id: 'legacy-projection-grant-must-not-win' }],
          next_actions: ['index', 'activate', 'rollback'],
          stale: false,
        },
      } as never),
    );
    core.listRuns.and.returnValue(
      of({
        items: [
          {
            knowledge_index_id: indexId,
            status: 'ready',
            etag: '"index-etag"',
            coverage: { percent: 100 },
            created_at: '2026-07-30T10:01:00Z',
            updated_at: '2026-07-30T10:02:00Z',
          },
        ],
        active: null,
        next_cursor: null,
      } as never),
    );
    core.startIndexRun.and.returnValue(of({ accepted: true } as never));
    core.activateIndex.and.returnValue(of({ accepted: true } as never));
    core.rollbackIndex.and.returnValue(of({ accepted: true } as never));

    governance.listIndexProfiles.and.returnValue(
      of({
        items: [
          {
            profile_id: 'profile-default',
            label: 'Default',
            description: 'Default profile',
            is_default: true,
            capabilities: [],
          },
        ],
        next_cursor: null,
        capabilities: [],
      }),
    );
    governance.listGrantPresets.and.returnValue(
      of({
        schema: 'ananta.source-control.grant-preset.v1',
        items: [
          {
            schema: 'ananta.source-control.grant-preset.v1',
            preset_id: 'preset-read',
            label: 'Read',
            description: 'Read-only context',
            operation: 'read',
            transformation: 'none',
            purpose: 'analysis',
            max_duration_seconds: 1800,
          },
        ],
        next_cursor: null,
        capabilities: [],
      } as never),
    );
    governance.listGrants.and.returnValue(
      of({
        schema: 'ananta.source-control.grant-admin-list.v1',
        items: [
          {
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
            policy_version: 7,
            state: 'active',
            issued_at: '2026-07-30T10:00:00Z',
            expires_at: '2026-07-30T10:30:00Z',
            expired: false,
            etag: '"grant-etag"',
          },
        ],
        next_cursor: null,
        capabilities: [],
      } as never),
    );
    governance.createGrant.and.returnValue(of({} as never));
    governance.revokeGrant.and.returnValue(of({} as never));

    TestBed.configureTestingModule({
      providers: [
        SourceDetailFacade,
        { provide: SourceControlV1ApiClient, useValue: core },
        { provide: SourceControlV1GovernanceApiClient, useValue: governance },
      ],
    });
  });

  it('loads catalogs and grants from the project and revision delivered by the Hub projection', () => {
    const facade = TestBed.inject(SourceDetailFacade);

    facade.load(connectionId);

    expect(governance.listIndexProfiles).toHaveBeenCalledOnceWith('project-alpha');
    expect(governance.listGrantPresets).toHaveBeenCalledOnceWith('project-alpha');
    expect(governance.listGrants).toHaveBeenCalledOnceWith('project-alpha');
    expect(facade.grants()[0]?.grantId).toBe(grantId);
    expect(facade.grants().some((grant) => grant.grantId.includes('legacy'))).toBeFalse();
    expect(facade.runs()[0]?.etag).toBe('"index-etag"');
  });

  it('starts, activates and rolls back only server-listed index resources with their ETags', () => {
    const facade = TestBed.inject(SourceDetailFacade);
    facade.load(connectionId);

    facade.startIndex('profile-default');
    expect(core.startIndexRun).toHaveBeenCalledWith(
      connectionId,
      'profile-default',
      jasmine.objectContaining({
        etag: '"connection-etag"',
        idempotencyKey: jasmine.stringMatching(/^ui:index:start:/),
      }),
    );

    facade.activateIndex(indexId);
    expect(core.activateIndex).toHaveBeenCalledWith(
      indexId,
      jasmine.objectContaining({
        etag: '"index-etag"',
        idempotencyKey: jasmine.stringMatching(/^ui:index:activate:/),
      }),
    );

    facade.rollbackIndex(indexId);
    expect(core.rollbackIndex).toHaveBeenCalledWith(
      indexId,
      jasmine.objectContaining({
        etag: '"index-etag"',
        idempotencyKey: jasmine.stringMatching(/^ui:index:rollback:/),
      }),
    );
  });

  it('creates and revokes grants with policy/grant CAS and no browser-invented source IDs', () => {
    const facade = TestBed.inject(SourceDetailFacade);
    facade.load(connectionId);

    facade.createGrant({
      destinationId: 'hub-destination-primary',
      policyId: 'policy-primary',
      presetId: 'preset-read',
      durationSeconds: 900,
      policyEtag: '"policy-etag"',
    });

    expect(governance.createGrant).toHaveBeenCalledWith(
      'project-alpha',
      {
        source_revision_id: revisionId,
        destination_id: 'hub-destination-primary',
        policy_id: 'policy-primary',
        preset_id: 'preset-read',
        duration_seconds: 900,
      },
      '"policy-etag"',
      jasmine.stringMatching(/^ui:grant:create:/),
    );

    facade.revokeGrant(grantId);
    expect(governance.revokeGrant).toHaveBeenCalledWith(
      'project-alpha',
      grantId,
      { reason_code: 'operator_revoked' },
      '"grant-etag"',
      jasmine.stringMatching(/^ui:grant:revoke:/),
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
      policyEtag: '"policy-etag"',
    });

    expect(core.startIndexRun).not.toHaveBeenCalled();
    expect(core.activateIndex).not.toHaveBeenCalled();
    expect(governance.createGrant).not.toHaveBeenCalled();
  });
});
