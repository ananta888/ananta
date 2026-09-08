import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, firstValueFrom, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetBrowserWorkspaceComponent } from './meet-browser-workspace.component';
import { MeetBrowserStatus, browserCommand, validateBrowserStatus } from './meet-browser-workspace';

const status = (): MeetBrowserStatus => ({ schema: 'ananta.meet-browser-status.v1', dialog_task_id: 'dialog', revision: 2,
  mode: 'off', task_id: 'browser-task', deadline_ms: 1788900000000, task_status: 'in_progress' });

describe('Closed task-bound browser metadata and commands', () => {
  it('copies status without treating selection as publication authority', () => {
    const source = status(), parsed = validateBrowserStatus(source, 'dialog');
    expect(parsed).toEqual(source); expect(parsed).not.toBe(source);
    expect(validateBrowserStatus({ ...source, mode: 'browser', task_status: 'failed' }, 'dialog').task_status).toBe('failed');
    expect(validateBrowserStatus({ ...source, mode: 'status', task_id: null, deadline_ms: null, task_status: null }, 'dialog').mode).toBe('status');
  });
  it.each([
    { dialog_task_id: 'foreign' }, { task_id: 'https://foreign' }, { revision: 0 }, { revision: 1024 }, { revision: true },
    { mode: 'desktop' }, { mode: 'status' }, { deadline_ms: null }, { task_status: 'published' }, { grant: 'not allowed' },
    { task_id: null }, { schema: 'unknown' },
  ])('rejects inconsistent, unbound or expanded receipts %#', changes => {
    expect(() => validateBrowserStatus({ ...status(), ...changes }, 'dialog')).toThrow('meet_browser_status_invalid');
  });
  it.each([
    { action: 'click', expected_revision: 1 }, { action: 'present', expected_revision: true },
    { action: 'status', expected_revision: 1023 }, { action: 'stop', expected_revision: 1, url: 'https://example.com' },
    { action: 'navigate', expected_revision: 1, url: 'file:///etc/passwd' },
    { action: 'navigate', expected_revision: 1, url: 'https://example.com/\n' },
    { action: 'navigate', expected_revision: 1, url: 'https://example.com/' + 'x'.repeat(2048) },
  ])('rejects malformed command without inferring permission %#', value => {
    expect(() => browserCommand(value)).toThrow('meet_browser_control_invalid');
  });
  it('recognizes only explicit browser option with independent screen capability', () => {
    const value = { schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'dialog', status: 'in_progress', deadline: 100,
      capabilities: ['screen.publish'], browser_workspace: true as const, controls: { revision: 1,
        chat: { enabled: false, revision: 1, since: 1 }, audio: { enabled: false, revision: 1, since: 1 },
        screen: { enabled: true, revision: 1, since: 1 } } };
    expect(validateDialog(value)).toBe(value);
    expect(() => validateDialog({ ...value, browser_workspace: 1 } as never)).toThrow();
    expect(() => validateDialog({ ...value, capabilities: [] })).toThrow();
  });
});

describe('Browser API uses only bounded owner Hub endpoints', () => {
  it('separates read, navigation and presentation with exact parent receipt validation', async () => {
    const core = { request: vi.fn(() => of(status())) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const api = TestBed.inject(MeetDialogApiService);
    expect(await firstValueFrom(api.browserStatus('p / one', 'dialog'))).toEqual(status());
    expect(core.request).toHaveBeenLastCalledWith('GET', 'https://hub.test/api/meet/v1/projects/p%20%2F%20one/dialogs/dialog/browser', 'https://hub.test', { body: undefined });
    const body = { action: 'navigate', expected_revision: 1, url: 'https://example.com' };
    await firstValueFrom(api.browserCommand('p', 'dialog', body));
    expect(core.request).toHaveBeenLastCalledWith('POST', 'https://hub.test/api/meet/v1/projects/p/dialogs/dialog/browser', 'https://hub.test', { body });
    const calls = core.request.mock.calls.length;
    await expect(firstValueFrom(api.browserCommand('p', 'dialog', { action: 'click' }))).rejects.toThrow('meet_browser_control_invalid');
    expect(core.request).toHaveBeenCalledTimes(calls);
    await expect(firstValueFrom(api.browserStatus('p', 'foreign'))).rejects.toThrow('meet_browser_status_invalid');
  });
});

describe('Passive feature-local browser controls', () => {
  let api: { browserStatus: ReturnType<typeof vi.fn>; browserCommand: ReturnType<typeof vi.fn> };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    identity = new BehaviorSubject({ sub: 'owner' });
    api = { browserStatus: vi.fn(() => of(status())), browserCommand: vi.fn(() => of(status())) };
    TestBed.configureTestingModule({ imports: [MeetBrowserWorkspaceComponent], providers: [
      { provide: MeetDialogApiService, useValue: api }, { provide: UserAuthService, useValue: { user$: identity } },
    ] });
  });
  function setup() {
    const f = TestBed.createComponent(MeetBrowserWorkspaceComponent);
    f.componentRef.setInput('projectId', 'project'); f.componentRef.setInput('taskId', 'dialog');
    f.componentRef.setInput('taskStatus', 'in_progress'); f.detectChanges(); return f;
  }
  it('never starts/captures/navigates on load and separates explicit headless actions', () => {
    const f = setup(), c = f.componentInstance;
    expect(api.browserStatus).not.toHaveBeenCalled(); expect(api.browserCommand).not.toHaveBeenCalled();
    expect(f.nativeElement.querySelector('iframe, video, canvas, img')).toBeNull();
    f.nativeElement.querySelector('button').click(); f.detectChanges();
    expect(api.browserStatus).toHaveBeenCalledExactlyOnceWith('project', 'dialog');
    c.url = 'https://example.com/docs'; c.navigate();
    expect(api.browserCommand).toHaveBeenCalledExactlyOnceWith('project', 'dialog', { action: 'navigate', expected_revision: 2, url: c.url });
    c.change('present');
    expect(api.browserCommand).toHaveBeenLastCalledWith('project', 'dialog', { action: 'present', expected_revision: 2 });
    expect(f.nativeElement.textContent).toContain('keine Steuerrechte');
    expect(f.nativeElement.textContent).toContain('keine sichtbare Zustellung');
  });
  it('does not retry uncertain writes or show remote error details', () => {
    const f = setup(), c = f.componentInstance;
    c.load(); c.url = 'https://example.com';
    api.browserCommand.mockReturnValue(throwError(() => ({ status: 503, message: 'SYNTHETIC_PRIVATE' })));
    c.navigate(); c.navigate(); f.detectChanges();
    expect(api.browserCommand).toHaveBeenCalledOnce(); expect(c.state()).toBeNull();
    expect(c.message()).toContain('bereits laufen'); expect(f.nativeElement.textContent).not.toContain('SYNTHETIC_PRIVATE');
  });
  it.each(['projectId', 'taskId', 'taskStatus', 'identity', 'destroy'])('clears URL and drops pending response on %s', change => {
    const f = setup(), c = f.componentInstance, pending = new Subject<MeetBrowserStatus>();
    api.browserStatus.mockReturnValue(pending); c.url = 'https://example.com'; c.load();
    if (change === 'identity') identity.next({ sub: 'other' });
    else if (change === 'destroy') f.destroy();
    else { f.componentRef.setInput(change, 'changed'); f.detectChanges(); }
    pending.next(status()); expect(pending.observed).toBe(false); expect(c.state()).toBeNull(); expect(c.url).toBe('');
  });
  it('prevents concurrent requests and cannot present failed or terminal browser Tasks', () => {
    const f = setup(), c = f.componentInstance, pending = new Subject<MeetBrowserStatus>();
    api.browserStatus.mockReturnValue(pending); c.load(); c.load(); expect(api.browserStatus).toHaveBeenCalledOnce();
    pending.next({ ...status(), task_status: 'failed' }); pending.complete(); c.change('present');
    expect(api.browserCommand).not.toHaveBeenCalled();
    f.componentRef.setInput('taskStatus', 'cancelled'); f.detectChanges(); c.load(); c.navigate();
    expect(api.browserStatus).toHaveBeenCalledOnce(); expect(api.browserCommand).not.toHaveBeenCalled();
  });
});
