import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { ContextAccessPolicyFacade } from './context-access-policy.facade';
import {
  PolicyOverviewComponent,
  authoritativeProjectIdFromRoute,
} from './policy-overview.component';

describe('authoritativeProjectIdFromRoute', () => {
  it('uses route params or resolved project data but never query params', () => {
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
    expect(authoritativeProjectIdFromRoute(noRouteContext)).toBeNull();
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
    selectedPolicy: signal(null),
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
    initialize: jasmine.createSpy(),
    reload: jasmine.createSpy(),
    loadLatest: jasmine.createSpy(),
    validateSelected: jasmine.createSpy(),
    safeReloadAfterConflict: jasmine.createSpy(),
  };

  beforeEach(async () => {
    facade.initialize.calls.reset();
    facade.reload.calls.reset();
    facade.loadLatest.calls.reset();
    facade.validateSelected.calls.reset();
    facade.safeReloadAfterConflict.calls.reset();
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
    const matrix = fixture.nativeElement.querySelector('.matrix-section table') as HTMLTableElement;
    expect(matrix.querySelector('caption')?.textContent).toContain('servergelieferte Regeln');
    expect(matrix.textContent).toContain('native_ananta_worker');
    expect(matrix.textContent).toContain('docker_container');
    expect(matrix.textContent).toContain('private_remote');
    expect(fixture.nativeElement.querySelector('[data-action="allow"]')).toBeNull();
  });

  it('keeps the grant assistant unavailable when the server has no catalogs or preview', () => {
    const grant = fixture.nativeElement.querySelector('.grant-section') as HTMLElement;
    const button = grant.querySelector('button') as HTMLButtonElement;
    expect(button.disabled).toBeTrue();
    expect(grant.textContent).toContain('keine Preview- oder Grant-Route');
    expect(grant.textContent).toContain('lokal niemals erlaubt');
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
    for (const flow of ['draft', 'preview', 'activate', 'revoke', 'rollback', 'grant']) {
      const card = fixture.nativeElement.querySelector(`[data-flow="${flow}"]`) as HTMLElement;
      expect(card.textContent).toContain('Nicht verfügbar');
    }
  });
});
