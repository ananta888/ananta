import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, firstValueFrom, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { MeetDialogDiagnosticsComponent } from './meet-dialog-diagnostics.component';
import { MeetDialogDiagnostics, validateDialogDiagnostics } from './meet-dialog-diagnostics';

function record(): MeetDialogDiagnostics {
  return { schema: 'ananta.meet-dialog-diagnostics.v1', task_id: 'task', classification: 'unverified_worker_observation',
    observation_status: 'recorded', hub_task_status: 'failed', recorded_at_ms: 1000, hub_control_revision_at_receipt: 2,
    observation: { schema: 'ananta.meet-dialog-terminal-observation.v1', stop_reason: 'control_stale', measurements: {
      elapsed_ms: 2000, self_cpu_ms: 300, terminated_children_cpu_ms: 500, self_max_rss_kib: 600, terminated_child_max_rss_kib: 700,
    } } };
}
function missing(): MeetDialogDiagnostics {
  return { ...record(), observation_status: 'missing', observation: null, recorded_at_ms: null, hub_control_revision_at_receipt: null };
}

describe('Closed unverified terminal diagnostic projection', () => {
  it('keeps absent reports and absent measurements distinct from zero or success', () => {
    expect(validateDialogDiagnostics(record(), 'task')).toEqual(record());
    expect(validateDialogDiagnostics(missing(), 'task')).toEqual(missing());
    const absent = { ...record(), observation: { ...record().observation!, measurements: null } };
    expect(validateDialogDiagnostics(absent, 'task')).toEqual(absent);
    expect(validateDialogDiagnostics({ ...missing(), hub_task_status: 'in_progress' }, 'task').observation).toBeNull();
  });
  it.each([
    null, {}, [], { ...record(), task_id: 'other' }, { ...record(), token: 'SYNTHETIC_PRIVATE' },
    { ...record(), classification: 'grounded' }, { ...record(), schema: 'other' }, { ...record(), observation_status: 'verified' },
    { ...record(), hub_task_status: 'in_progress' }, { ...record(), hub_task_status: 'approved' },
    { ...record(), recorded_at_ms: true }, { ...record(), recorded_at_ms: 0 }, { ...record(), recorded_at_ms: Number.MAX_SAFE_INTEGER + 1 },
    { ...record(), hub_control_revision_at_receipt: 0 }, { ...record(), hub_control_revision_at_receipt: 1024 },
    { ...record(), observation: null }, { ...record(), observation: { ...record().observation, text: 'SYNTHETIC_PRIVATE' } },
    { ...record(), observation: { ...record().observation, stop_reason: 'private_error' } },
    { ...record(), observation: { ...record().observation, measurements: {} } },
    { ...missing(), observation: record().observation }, { ...missing(), recorded_at_ms: 1 }, { ...missing(), hub_control_revision_at_receipt: 1 },
  ])('rejects content, forged authority and inconsistent data %#', value => {
    expect(() => validateDialogDiagnostics(value, 'task')).toThrow('meet_dialog_diagnostics_contract_invalid');
  });
  it.each([true, -1, 0.5, Number.NaN, Number.POSITIVE_INFINITY, '1', 1_000_000_000])('rejects invalid numeric metric %s', bad => {
    for (const field of Object.keys(record().observation!.measurements!)) {
      const value = record(); (value.observation!.measurements as unknown as Record<string, unknown>)[field] = bad;
      expect(() => validateDialogDiagnostics(value, 'task')).toThrow('meet_dialog_diagnostics_contract_invalid');
    }
  });
});

describe('Separate bodyless Hub diagnostic read', () => {
  it('uses the selected Hub and exact returned task without changing old endpoints', async () => {
    const core = { request: vi.fn(() => of(record())) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const api = TestBed.inject(MeetDialogApiService);
    expect(await firstValueFrom(api.diagnostics('p / one', 'task'))).toEqual(record());
    expect(core.request).toHaveBeenCalledExactlyOnceWith('GET',
      'https://hub.test/api/meet/v1/projects/p%20%2F%20one/dialogs/task/diagnostics', 'https://hub.test', { body: undefined });
    await expect(firstValueFrom(api.diagnostics('p', 'foreign'))).rejects.toThrow('meet_dialog_diagnostics_contract_invalid');
  });
});

describe('Isolated terminal diagnostic display', () => {
  let api: { diagnostics: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn> };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    api = { diagnostics: vi.fn(() => of(record())), stop: vi.fn(), start: vi.fn() };
    identity = new BehaviorSubject({ sub: 'owner' });
    TestBed.configureTestingModule({ imports: [MeetDialogDiagnosticsComponent], providers: [
      { provide: MeetDialogApiService, useValue: api }, { provide: UserAuthService, useValue: { user$: identity } },
    ] });
  });
  function setup() {
    const f = TestBed.createComponent(MeetDialogDiagnosticsComponent);
    f.componentRef.setInput('projectId', 'project'); f.componentRef.setInput('taskId', 'task');
    f.componentRef.setInput('taskStatus', 'failed'); f.detectChanges(); return f;
  }
  it('is passive until requested and labels independent CPU/RSS, missing GPU and unverified reports', () => {
    const f = setup(); expect(api.diagnostics).not.toHaveBeenCalled();
    f.nativeElement.querySelector('button').click(); f.detectChanges();
    expect(api.diagnostics).toHaveBeenCalledExactlyOnceWith('project', 'task');
    const text = f.nativeElement.textContent;
    expect(text).toContain('Nicht verifizierte Worker-Meldung'); expect(text).toContain('Steuerzustand nicht mehr frisch');
    expect(text).toContain('300 ms'); expect(text).toContain('500 ms'); expect(text).toContain('600 KiB'); expect(text).toContain('700 KiB');
    expect(text).toContain('kein gleichzeitiger Gesamtspeicher'); expect(text).toContain('keine GPU-Messung');
    expect(text).toContain('Keine Freigabe-Evidenz');
    expect(api.start).not.toHaveBeenCalled(); expect(api.stop).not.toHaveBeenCalled();
  });
  it('shows missing report or measurement without inventing zero or retrying', () => {
    const f = setup(); api.diagnostics.mockReturnValue(of(missing()));
    f.componentInstance.load(); f.detectChanges(); expect(f.nativeElement.textContent).toContain('Keine Abschlussdiagnose gespeichert');
    expect(f.nativeElement.querySelector('dl')).toBeNull();
    api.diagnostics.mockReturnValue(of({ ...record(), observation: { ...record().observation, measurements: null } }));
    f.componentInstance.load(); f.detectChanges(); expect(f.nativeElement.textContent).toContain('Keine Ressourcenmesswerte verfügbar');
    expect(f.nativeElement.querySelector('dl')).toBeNull();
  });
  it('does not expose unavailable endpoint details or retain older data on failure', () => {
    const f = setup(); f.componentInstance.load();
    api.diagnostics.mockReturnValue(throwError(() => ({ status: 404, message: 'SYNTHETIC_PRIVATE' })));
    f.componentInstance.load(); f.detectChanges();
    expect(f.componentInstance.diagnostics()).toBeNull(); expect(f.componentInstance.busy()).toBe(false);
    expect(f.nativeElement.textContent).not.toContain('SYNTHETIC_PRIVATE'); expect(api.diagnostics).toHaveBeenCalledTimes(2);
    expect(f.nativeElement.textContent).toContain('normalen Auftragsfunktionen bleiben unabhängig');
  });
  it.each(['projectId', 'taskId', 'taskStatus', 'disabled', 'identity', 'destroy'])('drops in-flight data on %s', change => {
    const f = setup(), pending = new Subject<MeetDialogDiagnostics>(); api.diagnostics.mockReturnValue(pending);
    f.componentInstance.load(); f.componentInstance.load(); expect(api.diagnostics).toHaveBeenCalledOnce();
    if (change === 'identity') identity.next({ sub: 'another' });
    else if (change === 'destroy') f.destroy();
    else { f.componentRef.setInput(change, change === 'disabled' ? true : 'changed'); f.detectChanges(); }
    pending.next(record()); expect(f.componentInstance.diagnostics()).toBeNull(); expect(f.componentInstance.busy()).toBe(false);
  });
});
