import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ContextAccessPolicyFacade } from './context-access-policy.facade';
import {
  PolicyOverviewComponent,
  authoritativeProjectIdFromRoute,
} from './policy-overview.component';

describe('authoritativeProjectIdFromRoute', () => {
  it('uses route params and falls back to query params if no explicit route context exists', () => {
    const route = {
      data: {},
      paramMap: convertToParamMap({ projectId: 'project-route' }),
      queryParamMap: convertToParamMap({ projectId: 'project-query' }),
      parent: null,
    } as unknown as ActivatedRoute['snapshot'];
    expect(authoritativeProjectIdFromRoute(route)).toBe('project-route');

    const noRouteContext = {
      data: {},
      paramMap: convertToParamMap({}),
      queryParamMap: convertToParamMap({ projectId: 'project-query' }),
      parent: null,
    } as unknown as ActivatedRoute['snapshot'];
    expect(authoritativeProjectIdFromRoute(noRouteContext)).toBe('project-query');
  });
});

describe('PolicyOverviewComponent', () => {
  let fixture: ComponentFixture<PolicyOverviewComponent>;
  const listError = signal<any>(null);
  const detailError = signal<any>(null);
  const validationError = signal<any>(null);
  const policies = signal<any[]>([{
      policy_id: 'policy-server',
      version: 2,
      project_id: 'project-a',
      scope: 'project',
      updated_at: '2026-01-01T00:00:00Z',
      policy: { rules: [] },
    }]);
  const facade = {
    policies,
    selectedPolicy: signal<any>(null),
    validation: signal(null),
    matrixRows: signal([{
      policyId: 'policy-server',
      version: 2,
      ruleId: 'rule-server',
      source: 'types=[docs]',
      sensitivity: 'project_internal',
      workerKinds: 'allowed=[native_ananta_worker]',
      runtimeKinds: 'allowed=[docker_container]',
      providerLocations: 'allowed=[private]',
      modelScopes: 'allowed=[private_remote]',
      operations: 'read_allowed=true',
      transformations: 'redaction_required=true',
      reasonData: 'Kein Reason-Code geliefert',
    }]),
    listLoading: signal(false),
    detailLoading: signal(false),
    validationLoading: signal(false),
    listConfirmed: signal(true),
    detailConfirmed: signal(false),
    validationConfirmed: signal(false),
    recordsTruncated: signal(false),
    matrixTruncated: signal(false),
    listError,
    detailError,
    validationError,
    managementAuthorized: signal(true),
    // The governance center grew a preview, an effective-matrix and a
    // lifecycle-mutation surface; this double had stayed on the older shape,
    // so every test died on `facade.matrixLoading is not a function` before it
    // could assert anything.
    preview: signal(null),
    effectiveMatrix: signal([]),
    matrixLoading: signal(false),
    mutationLoading: signal(false),
    matrixError: signal(null),
    loadEffectiveMatrix: vi.fn(),
    previewSelected: vi.fn(),
    activateSelected: vi.fn(),
    revokeSelected: vi.fn(),
    rollbackSelected: vi.fn(),
    initialize: vi.fn(),
    reload: vi.fn(),
    loadLatest: vi.fn(),
    validateSelected: vi.fn(),
    safeReloadAfterConflict: vi.fn(),
  };

  beforeEach(async () => {
    facade.initialize.mockReset();
    facade.reload.mockReset();
    facade.loadLatest.mockReset();
    facade.validateSelected.mockReset();
    facade.safeReloadAfterConflict.mockReset();
    listError.set(null);
    detailError.set(null);
    validationError.set(null);
    policies.set([{
      policy_id: 'policy-server',
      version: 2,
      project_id: 'project-a',
      scope: 'project',
      updated_at: '2026-01-01T00:00:00Z',
      policy: { rules: [] },
    }]);
    facade.listLoading.set(false);
    facade.listConfirmed.set(true);
    facade.managementAuthorized.set(true);
    facade.mutationLoading.set(false);
    facade.selectedPolicy.set(null);
    await TestBed.configureTestingModule({
      imports: [PolicyOverviewComponent],
      providers: [
        { provide: ActivatedRoute, useValue: { snapshot: {
          data: { projectId: 'project-a' },
          paramMap: convertToParamMap({}),
          parent: null,
        } } },
        { provide: ContextAccessPolicyFacade, useValue: facade },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(PolicyOverviewComponent);
    fixture.detectChanges();
  });

  it('initializes only from the authoritative route project', () => {
    expect(facade.initialize).toHaveBeenCalledWith('project-a');
    expect(fixture.nativeElement.textContent).not.toContain('default-project');
  });

  it('renders a bounded accessible matrix from server rows without an allow control', () => {
    // The governance center renders the table from facade.effectiveMatrix(),
    // one row per server-resolved source/destination pair.
    facade.effectiveMatrix.set([{
      schema: 'ananta.source-control.access-decision.v1',
      source_revision_id: 'rev-server',
      revision_digest: 'digest-server',
      destination_id: 'dest-server',
      operation: 'chat_context',
      transformation: 'redaction_required',
      purpose: 'review',
      decision: 'deny',
      reason_codes: ['policy_denied'],
      matched_rule_path: ['rules', '0'],
      default_applied: false,
      approval_requirement: null,
      policy_digest: 'policy-digest-server',
    }]);
    fixture.detectChanges();

    const matrix = fixture.nativeElement.querySelector('.matrix-section table') as HTMLTableElement;
    expect(matrix.querySelector('caption')?.textContent).toContain('serverseitig ausgewertete');
    expect(matrix.textContent).toContain('rev-server');
    expect(matrix.textContent).toContain('dest-server');
    expect(matrix.textContent).toContain('policy_denied');
    // The UI never offers to grant access itself; only the server decides.
    expect(fixture.nativeElement.querySelector('[data-action="allow"]')).toBeNull();
  });

  it('keeps the grant assistant unavailable when the server has no catalogs or preview', () => {
    const grant = fixture.nativeElement.querySelector('.grant-section') as HTMLElement;
    const button = grant.querySelector('button') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    // Presets are the flow the Hub has no route for; preview does have one.
    expect(grant.textContent).toContain('keinen Preset-Katalog');
    // A grant mutation route still does not exist, and the assistant says so.
    expect(grant.textContent).toContain('Grant-Mutationsroute fehlt');
  });

  it('offers only a safe reload action for a 409 conflict', () => {
    detailError.set({
      state: 'conflict',
      message: 'server changed',
      reasonCode: 'server_version_conflict',
      conflict: true,
    });
    fixture.detectChanges();
    const alert = fixture.nativeElement.querySelector('.detail-section [role="alert"]') as HTMLElement;
    expect(alert.textContent).toContain('server_version_conflict');
    const button = alert.querySelector('button') as HTMLButtonElement;
    expect(button.textContent).toContain('Sicher neu laden');
    button.click();
    expect(facade.safeReloadAfterConflict).toHaveBeenCalled();
  });

  it('announces loading through a live status region', () => {
    facade.listLoading.set(true);
    fixture.detectChanges();
    const status = fixture.nativeElement.querySelector('[role="status"][aria-live="polite"]') as HTMLElement;
    expect(status.textContent).toContain('werden geladen');
  });

  it('renders an explicit empty state after a confirmed empty response', () => {
    policies.set([]);
    facade.listConfirmed.set(true);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('keine Policy-Datensätze');
  });

  it('communicates lifecycle availability with text in addition to color', () => {
    // Colour alone would not be readable; each card has to say which it is.
    // The Hub serves the lifecycle routes, so those must not read as absent.
    for (const flow of ['draft', 'preview', 'activate', 'revoke', 'rollback']) {
      const card = fixture.nativeElement.querySelector(`[data-flow="${flow}"]`) as HTMLElement;
      expect(card.textContent).toContain('Route vorhanden');
      expect(card.textContent).not.toContain('Nicht verfügbar');
    }
    for (const flow of ['presets', 'destinations', 'grant']) {
      const card = fixture.nativeElement.querySelector(`[data-flow="${flow}"]`) as HTMLElement;
      expect(card.textContent).toContain('Nicht verfügbar');
    }
  });

  it('disables lifecycle mutations while Hub authorization is being refreshed', () => {
    facade.selectedPolicy.set({
      policy_id: 'policy-server',
      version: 2,
      scope: 'project',
      policy: { rules: [] },
    });
    facade.managementAuthorized.set(false);
    fixture.detectChanges();

    const labels = ['Aktivieren', 'Widerrufen', 'Rollback als neuen Draft erstellen'];
    for (const label of labels) {
      const button = Array.from(
        fixture.nativeElement.querySelectorAll(
          '.lifecycle-actions button',
        ) as NodeListOf<HTMLButtonElement>,
      ).find((candidate) => candidate.textContent?.trim() === label);
      expect(button?.disabled).toBe(true);
    }
  });
});
