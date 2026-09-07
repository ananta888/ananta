import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetDialogComponent } from './meet-dialog.component';
import { UserAuthService } from '../../services/user-auth.service';

const row = () => ({ schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['chat.read', 'chat.send'], controls: { revision: 1, chat: { enabled: true, revision: 1, since: 1000 },
    audio: { enabled: false, revision: 1, since: 1000 }, screen: { enabled: false, revision: 1, since: 1000 } } });

describe('Hub-owned Meet dialog controls', () => {
  it('validates and toggles optional speech without resetting unrelated controls', () => {
    const source = { ...row(), capabilities: [...row().capabilities, 'speech.publish'],
      controls: { ...row().controls, speech: { enabled: true, revision: 1, since: 1000 } } };
    expect(validateDialog(source)).toBe(source);
    const result = { ...source, controls: { ...source.controls, revision: 2, speech: { enabled: false, revision: 2, since: 1100 } } };
    const api = { control: vi.fn(() => of(result)) };
    TestBed.configureTestingModule({ providers: [{ provide: MeetDialogApiService, useValue: api },
      { provide: UserAuthService, useValue: { user$: of({ id: 'u' }) } }] });
    const component = TestBed.runInInjectionContext(() => new MeetDialogComponent());
    component.projectId = 'p'; component.dialogs.set([source]); component.toggle(source, 'speech');
    expect(api.control).toHaveBeenCalledWith('p', 'task', { expected_revision: 1, chat: true, audio: false, screen: false, speech: false });
    expect(component.dialogs()[0].controls.chat).toEqual(source.controls.chat);
    expect(component.canControl(row(), 'speech')).toBe(false);
    expect(() => validateDialog({ ...source, capabilities: row().capabilities })).toThrow();
    expect(() => validateDialog({ ...source, controls: { ...source.controls, speech: null } } as never)).toThrow();
    component.ngOnDestroy();
  });
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
    expect(c.chat || c.audio || c.screen || c.speech || c.avatar).toBe(false); c.start(); expect(api.start).not.toHaveBeenCalled();
  });
  it('requires explicit chat and speech selection without enabling listening or capture', async () => {
    const f = setup(), c = f.componentInstance;
    await f.whenStable();
    const checkbox = [...f.nativeElement.querySelectorAll('input[type="checkbox"]')]
      .find((element: HTMLInputElement) => element.parentElement?.textContent?.includes('lokaler KI-Stimme')) as HTMLInputElement;
    expect(checkbox.disabled).toBe(true); expect(checkbox.checked).toBe(false);
    c.setChat(true); f.detectChanges(); await f.whenStable(); expect(checkbox.disabled).toBe(false);
    c.speech = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['chat.read', 'chat.send', 'speech.publish'],
      duration_seconds: 900, chat_mode: 'mention', audio_mode: 'off' });
    c.setChat(false); expect(c.speech).toBe(false);
  });
  it('never infers chat permission from a stale speech selection', () => {
    const c = setup().componentInstance; c.screen = true; c.speech = true; c.start();
    expect(api.start).not.toHaveBeenCalled(); expect(c.message()).toContain('ausdrücklich ausgewählten Raumchat');
  });
  it('requests only explicit neutral-avatar permission and does not activate it on start', () => {
    const c = setup().componentInstance; c.avatar = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['avatar.publish'],
      duration_seconds: 900, chat_mode: 'off', audio_mode: 'off' });
    expect(api.control).not.toHaveBeenCalled();
  });
  it('toggles the assigned avatar while preserving independent speech and chat state', () => {
    const c = setup().componentInstance;
    const source = { ...row(), capabilities: ['chat.read', 'chat.send', 'speech.publish', 'avatar.publish'],
      controls: { ...row().controls, speech: { enabled: true, revision: 1, since: 1000 },
        avatar: { enabled: false, revision: 1, since: 1000 } } };
    expect(validateDialog(source)).toBe(source); c.toggle(source, 'avatar');
    expect(api.control).toHaveBeenCalledWith('project', 'task', { expected_revision: 1, chat: true, audio: false,
      screen: false, speech: true, avatar: true });
    expect(c.canControl(row(), 'avatar')).toBe(false);
    expect(() => validateDialog({ ...source, capabilities: ['chat.read', 'chat.send', 'speech.publish'] })).toThrow();
    expect(() => validateDialog({ ...source, controls: { ...source.controls, avatar: null } } as never)).toThrow();
    expect(() => validateDialog({ ...source, controls: { ...source.controls, avatar: { ...source.controls.avatar, profile: 'url' } } } as never)).toThrow();
  });
  it.each(['account', 'project'])('clears selected neutral avatar on %s change', change => {
    const f = setup(), c = f.componentInstance; c.avatar = true;
    if (change === 'account') identity.next(null);
    else { f.componentRef.setInput('projectId', 'other'); f.detectChanges(); }
    expect(c.avatar).toBe(false); expect(api.start).not.toHaveBeenCalled();
  });
  it.each(['account', 'project'])('clears selected voice permission on %s change', change => {
    const f = setup(), c = f.componentInstance; c.setChat(true); c.speech = true;
    if (change === 'account') identity.next(null);
    else { f.componentRef.setInput('projectId', 'other'); f.detectChanges(); }
    expect(c.speech).toBe(false); expect(c.chat).toBe(false); expect(api.start).not.toHaveBeenCalled();
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
