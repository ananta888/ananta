import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { vi } from 'vitest';

import { SourceDetailComponent } from './source-detail.component';
import { SourceDetailFacade } from './source-detail.facade';

describe('SourceDetailComponent', () => {
  let fixture: ComponentFixture<SourceDetailComponent>;
  const connectionId = `conn_${'1'.repeat(64)}`;
  const indexId = `idx_${'2'.repeat(64)}`;
  const grantId = `grant_${'3'.repeat(64)}`;

  const facade = {
    sourceError: signal(null),
    source: signal({
      sourceId: connectionId,
      displayName: 'Primary source',
      sourceType: 'direct_text',
      status: 'ready',
      createdAt: '2026-07-30T10:00:00Z',
      updatedAt: '2026-07-30T10:01:00Z',
      metadata: {},
    }),
    loading: signal(false),
    active: signal(false),
    stale: signal(false),
    coveragePercent: signal(100),
    nextAction: signal('Index aktivieren'),
    revisionsError: signal(null),
    revisions: signal([]),
    revisionsTruncated: signal(false),
    indexError: signal(null),
    mutationError: signal(null),
    lifecycleMessage: signal(''),
    indexProfiles: signal([
      {
        profileId: 'profile-default',
        label: 'Default',
        description: 'Default profile',
        isDefault: true,
      },
    ]),
    mutationLoading: signal(false),
    runs: signal([
      {
        indexId,
        etag: '"index-etag"',
        status: 'ready',
        createdAt: '2026-07-30T10:00:00Z',
        updatedAt: '2026-07-30T10:01:00Z',
        coveragePercent: 100,
        stale: false,
        metadata: {},
      },
    ]),
    runsTruncated: signal(false),
    index: signal({
      indexId,
      etag: '"index-etag"',
      status: 'ready',
      createdAt: '2026-07-30T10:00:00Z',
      updatedAt: '2026-07-30T10:01:00Z',
      coveragePercent: 100,
      stale: false,
      metadata: {},
    }),
    graphLoading: signal(false),
    graphError: signal(null),
    graphNodes: signal([]),
    graphTextAlternative: signal(''),
    artifactStatus: signal(null),
    graphEdges: signal([]),
    graphTruncated: signal(false),
    governanceError: signal(null),
    governanceLoading: signal(false),
    grantPresets: signal([
      {
        presetId: 'preset-read',
        label: 'Read',
        description: 'Read only',
        operation: 'read',
        transformation: 'none',
        purpose: 'analysis',
        maxDurationSeconds: 1800,
      },
    ]),
    grants: signal([
      {
        grantId,
        grantFamilyId: `grantfam_${'4'.repeat(64)}`,
        version: 1,
        sourceRevisionId: `srev_${'5'.repeat(64)}`,
        destinationId: 'hub-destination-primary',
        presetId: 'preset-read',
        operation: 'read',
        transformation: 'none',
        purpose: 'analysis',
        policyVersion: 1,
        state: 'active',
        issuedAt: '2026-07-30T10:00:00Z',
        expiresAt: '2026-07-30T10:30:00Z',
        expired: false,
        etag: '"grant-etag"',
      },
    ]),
    auditLoading: signal(false),
    auditError: signal(null),
    auditEvents: signal([]),
    load: vi.fn(),
    loadGraph: vi.fn(),
    loadAudit: vi.fn(),
    can: vi.fn(() => true),
    startIndex: vi.fn(),
    activateIndex: vi.fn(),
    rollbackIndex: vi.fn(),
    createGrant: vi.fn(),
    revokeGrant: vi.fn(),
    refresh: vi.fn(),
    scan: vi.fn(),
    disable: vi.fn(),
  };

  beforeEach(async () => {
    [
      facade.load,
      facade.loadGraph,
      facade.loadAudit,
      facade.startIndex,
      facade.activateIndex,
      facade.rollbackIndex,
      facade.createGrant,
      facade.revokeGrant,
      facade.refresh,
      facade.scan,
      facade.disable,
    ].forEach((spy) => spy.mockClear());

    await TestBed.configureTestingModule({
      imports: [SourceDetailComponent],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: convertToParamMap({ sourceId: connectionId }),
            },
          },
        },
        { provide: SourceDetailFacade, useValue: facade },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SourceDetailComponent);
    fixture.detectChanges();
  });

  it('loads the route-bound source and exposes keyboard-operable tabs', () => {
    expect(facade.load).toHaveBeenCalledWith(connectionId);
    const tabs = fixture.nativeElement.querySelectorAll('[role="tab"]') as NodeListOf<HTMLButtonElement>;
    tabs[0].focus();
    tabs[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab()).toBe('revisions');
    expect(document.activeElement).toBe(tabs[1]);
  });

  it('starts and transitions only the displayed server index/profile', () => {
    fixture.componentInstance.selectTab('runs');
    fixture.componentInstance.selectedIndexProfileId.set('profile-default');
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('[data-testid="index-start"]') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('[data-testid="index-activate"]') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('[data-testid="index-rollback"]') as HTMLButtonElement).click();

    expect(facade.startIndex).toHaveBeenCalledWith('profile-default');
    expect(facade.activateIndex).toHaveBeenCalledWith(indexId);
    expect(facade.rollbackIndex).toHaveBeenCalledWith(indexId);
  });

  it('passes explicit policy inputs to grant creation and revokes the server grant', () => {
    fixture.componentInstance.selectTab('access');
    fixture.componentInstance.grantDestinationId.set('hub-destination-primary');
    fixture.componentInstance.grantPolicyId.set('policy-primary');
    fixture.componentInstance.grantPolicyEtag.set('"policy-etag"');
    fixture.componentInstance.grantPresetId.set('preset-read');
    fixture.componentInstance.grantDurationSeconds.set(900);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('[data-testid="grant-create"]') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('[data-testid="grant-revoke"]') as HTMLButtonElement).click();

    expect(facade.createGrant).toHaveBeenCalledWith({
      destinationId: 'hub-destination-primary',
      policyId: 'policy-primary',
      policyEtag: '"policy-etag"',
      presetId: 'preset-read',
      durationSeconds: 900,
    });
    expect(facade.revokeGrant).toHaveBeenCalledWith(grantId);
  });
});
