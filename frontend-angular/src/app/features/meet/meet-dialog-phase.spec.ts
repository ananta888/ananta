import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, firstValueFrom, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { MeetDialogPhaseComponent } from './meet-dialog-phase.component';
import { MeetDialogPhase, validateDialogPhase } from './meet-dialog-phase';

function phase(): MeetDialogPhase {
  return { schema: 'ananta.meet-dialog-phase.v1', task_id: 'task', phase: 'publishing', revision: 5,
    task_status: 'in_progress', last_phase_at: 1000, observed_at: 1000, observation_fresh: true,
    publication_revision: 1, registered_sources: ['screen'] };
}

describe('Closed persisted phase projection', () => {
  it('accepts an exact historical observation and terminal history without a live assertion', () => {
    expect(validateDialogPhase(phase(), 'task')).toEqual(phase());
    const terminal = { ...phase(), phase: 'cancelled', task_status: 'cancelled', observation_fresh: false };
    expect(validateDialogPhase(terminal, 'task')).toEqual(terminal);
    expect(validateDialogPhase({ ...phase(), phase: 'connecting', observation_fresh: false, observed_at: null,
      publication_revision: null, registered_sources: [] }, 'task').phase).toBe('connecting');
  });
  it.each([
    null, {}, { ...phase(), grant: 'SYNTHETIC_PRIVATE' }, { ...phase(), task_id: 'other' },
    { ...phase(), schema: 'wrong' }, { ...phase(), revision: true }, { ...phase(), revision: 0 },
    { ...phase(), revision: Number.MAX_SAFE_INTEGER + 1 }, { ...phase(), phase: 'approved' },
    { ...phase(), last_phase_at: -1 }, { ...phase(), last_phase_at: Number.MAX_SAFE_INTEGER }, { ...phase(), task_status: 'cancelled' },
    { ...phase(), phase: 'cancelled' }, { ...phase(), observation_fresh: 1 },
    { ...phase(), observed_at: null }, { ...phase(), observed_at: 1001 }, { ...phase(), publication_revision: null },
    { ...phase(), registered_sources: ['human-camera'] }, { ...phase(), registered_sources: ['screen', 'screen'] },
    { ...phase(), registered_sources: [['screen']] }, { ...phase(), registered_sources: [] },
    { ...phase(), publication_revision: 0 }, { ...phase(), phase: 'joined' },
    { ...phase(), phase: 'connecting', observation_fresh: false },
    { ...phase(), phase: 'cancelled', task_status: 'cancelled' },
  ])('rejects malformed, foreign or inconsistent state %#', value => {
    expect(() => validateDialogPhase(value, 'task')).toThrow('meet_dialog_phase_contract_invalid');
  });
});

describe('Separate bounded Hub phase request', () => {
  it.each([false, true])('uses only bodyless read/observe and validates exact returned task: %s', async refresh => {
    const core = { request: vi.fn(() => of(phase())) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const api = TestBed.inject(MeetDialogApiService);
    expect(await firstValueFrom(api.phase('p / one', 'task', refresh))).toEqual(phase());
    expect(core.request).toHaveBeenCalledExactlyOnceWith(refresh ? 'POST' : 'GET',
      'https://hub.test/api/meet/v1/projects/p%20%2F%20one/dialogs/task/phase', 'https://hub.test', { body: undefined });
    await expect(firstValueFrom(api.phase('p', 'foreign'))).rejects.toThrow('meet_dialog_phase_contract_invalid');
  });
});

describe('Isolated phase display', () => {
  let api: { phase: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn> };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    api = { phase: vi.fn(() => of(phase())), stop: vi.fn(), start: vi.fn() };
    identity = new BehaviorSubject({ sub: 'owner' });
    TestBed.configureTestingModule({ imports: [MeetDialogPhaseComponent], providers: [
      { provide: MeetDialogApiService, useValue: api }, { provide: UserAuthService, useValue: { user$: identity } },
    ] });
  });
  function setup() {
    const fixture = TestBed.createComponent(MeetDialogPhaseComponent);
    fixture.componentRef.setInput('projectId', 'project'); fixture.componentRef.setInput('taskId', 'task');
    fixture.componentRef.setInput('taskStatus', 'in_progress'); fixture.detectChanges();
    return fixture;
  }
  it('does nothing on load and renders past-tense evidence only after an explicit headless click', () => {
    const f = setup(); expect(api.phase).not.toHaveBeenCalled();
    f.nativeElement.querySelector('button').click(); f.detectChanges();
    expect(api.phase).toHaveBeenCalledExactlyOnceWith('project', 'task', false);
    expect(f.nativeElement.textContent).toContain('Bei der Abfrage war diese Beobachtung frisch');
    expect(f.nativeElement.textContent).toContain('keine laufende Liveanzeige');
    expect(f.nativeElement.textContent).toContain('Revision 5');
    expect(api.start).not.toHaveBeenCalled(); expect(api.stop).not.toHaveBeenCalled();
  });
  it('uses a distinct refresh action without retrying unavailable old endpoints', () => {
    const f = setup(); api.phase.mockReturnValue(throwError(() => ({ status: 409, message: 'SYNTHETIC_PRIVATE' })));
    f.componentInstance.load(true); f.detectChanges();
    expect(api.phase).toHaveBeenCalledExactlyOnceWith('project', 'task', true);
    expect(f.componentInstance.phase()).toBeNull(); expect(f.componentInstance.busy()).toBe(false);
    expect(f.nativeElement.textContent).toContain('normalen Auftragsfunktionen bleiben unabhängig');
    expect(f.nativeElement.textContent).not.toContain('SYNTHETIC_PRIVATE');
  });
  it.each(['projectId', 'taskId', 'taskStatus', 'controlRevision', 'disabled', 'identity', 'destroy'])('drops pending results on %s', change => {
    const f = setup(), pending = new Subject<MeetDialogPhase>(); api.phase.mockReturnValue(pending);
    f.componentInstance.load(); expect(f.componentInstance.busy()).toBe(true);
    if (change === 'identity') identity.next({ sub: 'another' });
    else if (change === 'destroy') f.destroy();
    else { f.componentRef.setInput(change, change === 'disabled' ? true : change === 'controlRevision' ? 2 : 'changed'); f.detectChanges(); }
    pending.next(phase()); expect(f.componentInstance.phase()).toBeNull(); expect(f.componentInstance.busy()).toBe(false);
    expect(api.phase).toHaveBeenCalledOnce(); expect(api.stop).not.toHaveBeenCalled();
  });
  it('never refreshes a terminal task or issues a second simultaneous request', () => {
    const f = setup(), pending = new Subject<MeetDialogPhase>(); api.phase.mockReturnValue(pending);
    f.componentInstance.load(); f.componentInstance.load(true); expect(api.phase).toHaveBeenCalledOnce();
    f.componentRef.setInput('taskStatus', 'cancelled'); f.detectChanges(); f.componentInstance.load(true);
    expect(api.phase).toHaveBeenCalledOnce(); f.componentInstance.load(); expect(api.phase).toHaveBeenCalledTimes(2);
  });
});
