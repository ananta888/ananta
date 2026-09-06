import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetDialogComponent } from './meet-dialog.component';
import { UserAuthService } from '../../services/user-auth.service';

const row = () => ({ schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['chat.read', 'chat.send'], controls: { revision: 1, chat: { enabled: true, revision: 1, since: 1000 },
    audio: { enabled: false, revision: 1, since: 1000 }, screen: { enabled: false, revision: 1, since: 1000 } } });

describe('Hub-owned Meet dialog controls', () => {
  const api = { list: vi.fn(), start: vi.fn(), stop: vi.fn(), control: vi.fn() };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    identity = new BehaviorSubject({ sub: 'owner' });
    for (const method of Object.values(api)) method.mockReset();
    api.list.mockReturnValue(of({ schema: 'ananta.meet-dialog-list.v1', items: [row()], next_cursor: null }));
    api.start.mockReturnValue(of({ task_id: 'task' })); api.stop.mockReturnValue(of({ ...row(), status: 'cancelled' }));
    api.control.mockReturnValue(of(row()));
    TestBed.configureTestingModule({ imports: [MeetDialogComponent], providers: [
      { provide: MeetDialogApiService, useValue: api }, { provide: UserAuthService, useValue: { user$: identity } },
    ] });
  });
  function setup() {
    const f = TestBed.createComponent(MeetDialogComponent); f.componentRef.setInput('projectId', 'project'); f.detectChanges(); return f;
  }
  it('never starts, captures or grants sources on load; selections default off', () => {
    const f = setup(), c = f.componentInstance;
    expect(api.start).not.toHaveBeenCalled(); expect(api.list).not.toHaveBeenCalled();
    expect(c.chat || c.audio || c.screen).toBe(false); c.start(); expect(api.start).not.toHaveBeenCalled();
  });
  it('sends only selected rights to the Hub and restores the persisted task list', () => {
    const f = setup(), c = f.componentInstance;
    c.chat = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['chat.read', 'chat.send'], duration_seconds: 900,
      chat_mode: 'mention', audio_mode: 'off' });
    expect(c.dialogs()).toHaveLength(1); c.toggle(row(), 'chat');
    expect(api.control).toHaveBeenCalledWith('project', 'task', { expected_revision: 1, chat: false, audio: false, screen: false });
    c.stop(row()); expect(c.dialogs()[0].status).toBe('cancelled');
  });
  it.each(['account', 'project', 'destroy'])('drops late results on %s change without stopping an unrelated server task', change => {
    const stream = new Subject(); api.list.mockReturnValue(stream);
    const f = setup(), c = f.componentInstance; c.reload();
    if (change === 'account') identity.next(null);
    if (change === 'project') { f.componentRef.setInput('projectId', 'other'); f.detectChanges(); }
    if (change === 'destroy') f.destroy();
    stream.next({ items: [row()], next_cursor: null });
    expect(stream.observed).toBe(false); expect(c.dialogs()).toEqual([]); expect(api.stop).not.toHaveBeenCalled();
  });
  it('does not automatically retry an uncertain start or forge success', () => {
    api.start.mockReturnValue(throwError(() => ({ status: 503 })));
    const c = setup().componentInstance; c.screen = true; c.start();
    expect(api.start).toHaveBeenCalledOnce(); expect(c.message()).toContain('Ergebnis unklar'); expect(c.dialogs()).toEqual([]);
  });
  it('does not offer source activation outside the assigned capabilities', () => {
    const c = setup().componentInstance;
    expect(c.canControl(row(), 'chat')).toBe(true);
    for (const source of ['audio', 'screen'] as const) {
      expect(c.canControl(row(), source)).toBe(false); c.toggle(row(), source);
    }
    expect(api.control).not.toHaveBeenCalled();
  });
  it('rejects malformed controls, unexpected capabilities and non-string identities', () => {
    expect(validateDialog(row()).task_id).toBe('task');
    for (const value of [{ ...row(), task_id: true }, { ...row(), capabilities: ['record'] },
      { ...row(), controls: { ...row().controls, revision: true } }]) expect(() => validateDialog(value as never)).toThrow();
  });
});
