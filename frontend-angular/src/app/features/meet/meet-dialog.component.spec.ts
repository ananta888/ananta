import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, defer, of, throwError } from 'rxjs';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetDialogComponent } from './meet-dialog.component';
import { UserAuthService } from '../../services/user-auth.service';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import { effective } from './meet-avatar-test-fixtures';

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
  const api = { list: vi.fn(), start: vi.fn(), stop: vi.fn(), control: vi.fn(), selectAvatar: vi.fn(), selectVoice: vi.fn(), phase: vi.fn(), diagnostics: vi.fn() };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    identity = new BehaviorSubject({ sub: 'owner' });
    for (const method of Object.values(api)) method.mockReset();
    api.list.mockReturnValue(of({ schema: 'ananta.meet-dialog-list.v1', items: [row()], next_cursor: null }));
    api.start.mockReturnValue(of({ task_id: 'task' })); api.stop.mockReturnValue(of({ ...row(), status: 'cancelled' }));
    api.control.mockReturnValue(of(row()));
    TestBed.configureTestingModule({ imports: [MeetDialogComponent], providers: [
      { provide: MeetDialogApiService, useValue: api }, { provide: UserAuthService, useValue: { user$: identity } },
      { provide: PersonaProfileApiClient, useValue: { effective: vi.fn() } },
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
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
  it('negotiates browser workspace only explicitly and clears it with screen rights', () => {
    const c = setup().componentInstance;
    expect(c.browserWorkspace).toBe(false); c.setScreen(true); c.browserWorkspace = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['screen.publish'], duration_seconds: 900,
      chat_mode: 'off', audio_mode: 'off', browser_workspace: true });
    c.setScreen(false); expect(c.browserWorkspace).toBe(false);
    api.start.mockClear(); c.browserWorkspace = true; c.start();
    expect(api.start).not.toHaveBeenCalled(); expect(c.message()).toContain('Arbeitsansicht-Rechte');
  });
  it('keeps the parent stop usable while a child phase query is pending', () => {
    const f = setup(), pending = new Subject<never>(); api.phase.mockReturnValue(pending);
    f.componentInstance.reload(); f.detectChanges();
    f.nativeElement.querySelector('app-meet-dialog-phase button').click(); f.detectChanges();
    expect(api.phase).toHaveBeenCalledOnce(); expect(f.componentInstance.busy()).toBe(false);
    const stop = [...f.nativeElement.querySelectorAll('button')].find((button: HTMLButtonElement) =>
      button.textContent?.includes('KI-Auftrag vollständig stoppen')) as HTMLButtonElement;
    expect(stop.disabled).toBe(false); stop.click(); f.detectChanges();
    expect(api.stop).toHaveBeenCalledExactlyOnceWith('project', 'task');
    expect(f.nativeElement.querySelector('app-meet-dialog-phase').textContent).not.toContain('Revision');
  });
  it('keeps stop independent of pending diagnostics and discards the cancelled request', () => {
    const f = setup(), pending = new Subject<never>(); api.diagnostics.mockReturnValue(pending);
    f.componentInstance.reload(); f.detectChanges();
    f.nativeElement.querySelector('app-meet-dialog-diagnostics button').click(); f.detectChanges();
    expect(api.diagnostics).toHaveBeenCalledOnce(); expect(f.componentInstance.busy()).toBe(false);
    const stop = [...f.nativeElement.querySelectorAll('button')].find((button: HTMLButtonElement) =>
      button.textContent?.includes('KI-Auftrag vollständig stoppen')) as HTMLButtonElement;
    expect(stop.disabled).toBe(false); stop.click(); f.detectChanges();
    expect(api.stop).toHaveBeenCalledExactlyOnceWith('project', 'task'); expect(pending.observed).toBe(false);
    expect(f.nativeElement.querySelector('app-meet-dialog-diagnostics').textContent).not.toContain('Worker-Meldung');
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
  it('negotiates voice profiles only explicitly and clears them when speech or chat is disabled', () => {
    const c = setup().componentInstance; expect(c.voiceProfiles).toBe(false);
    c.setChat(true); c.setSpeech(true); c.voiceProfiles = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['chat.read', 'chat.send', 'speech.publish'],
      duration_seconds: 900, chat_mode: 'mention', audio_mode: 'off', voice_profiles: true });
    expect(api.control).not.toHaveBeenCalled(); c.setSpeech(false); expect(c.voiceProfiles).toBe(false);
    c.voiceProfiles = true; c.setChat(false); expect(c.voiceProfiles).toBe(false);
    api.start.mockClear(); c.screen = true; c.voiceProfiles = true; c.start();
    expect(api.start).not.toHaveBeenCalled(); expect(c.message()).toContain('ausdrücklich ausgewählte Sprachausgabe');
  });
  it('selects voices with independent passive CAS and never upgrades or retries old sessions', () => {
    const f = setup(), c = f.componentInstance;
    c.selectVoice(row(), null); expect(api.selectVoice).not.toHaveBeenCalled();
    const source = { ...row(), capabilities: [...row().capabilities, 'speech.publish'],
      voice_selection: { mode: 'configured-piper-v1' as const },
      controls: { ...row().controls, speech: { enabled: false, revision: 1, since: 1000 } } };
    c.dialogs.set([source]); f.detectChanges(); expect(f.nativeElement.querySelector('app-meet-voice-picker')).not.toBeNull();
    api.selectVoice.mockReturnValue(of({ ...source, controls: { ...source.controls, revision: 2 } }));
    c.selectVoice(source, effective().selection);
    expect(api.selectVoice).toHaveBeenCalledWith('project', 'task', { expected_revision: 1, profile: effective().selection });
    expect(api.control).not.toHaveBeenCalled(); expect(c.dialogs()[0].controls.speech?.enabled).toBe(false);
    expect(c.dialogs()[0].controls.chat).toEqual(source.controls.chat);
    api.selectVoice.mockReturnValue(throwError(() => ({ status: 409 })));
    c.selectVoice(c.dialogs()[0], null);
    expect(api.selectVoice).toHaveBeenLastCalledWith('project', 'task', { expected_revision: 2, configured: true });
    expect(api.selectVoice).toHaveBeenCalledTimes(2); expect(c.message()).toContain('Bitte aktualisieren');
    c.selectVoice({ ...source, status: 'cancelled' }, null); expect(api.selectVoice).toHaveBeenCalledTimes(2);
  });
  it('requests only explicit neutral-avatar permission and does not activate it on start', () => {
    const c = setup().componentInstance; c.avatar = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['avatar.publish'],
      duration_seconds: 900, chat_mode: 'off', audio_mode: 'off' });
    expect(api.control).not.toHaveBeenCalled();
  });
  it('negotiates image support only when explicitly selected and resets it with avatar permission', () => {
    const c = setup().componentInstance; expect(c.avatarImages).toBe(false);
    c.setAvatar(true); c.avatarImages = true; c.start();
    expect(api.start).toHaveBeenCalledWith('project', '', { capabilities: ['avatar.publish'], avatar_images: true,
      duration_seconds: 900, chat_mode: 'off', audio_mode: 'off' });
    expect(api.control).not.toHaveBeenCalled(); c.setAvatar(false); expect(c.avatarImages).toBe(false);
  });
  it('never turns a stale image checkbox into avatar capability', () => {
    const c = setup().componentInstance; c.screen = true; c.avatarImages = true; c.start();
    expect(api.start).not.toHaveBeenCalled(); expect(c.message()).toContain('ausdrücklich ausgewählten KI-Avatar');
  });
  it('selects a Hub pin by current CAS without activating a paused avatar or changing other sources', () => {
    const f = setup(), c = f.componentInstance;
    const source = { ...row(), capabilities: [...row().capabilities, 'avatar.publish'],
      avatar_selection: { mode: 'neutral-ai-v1' as const },
      controls: { ...row().controls, avatar: { enabled: false, revision: 1, since: 1000 } } };
    c.dialogs.set([source]); f.detectChanges(); expect(f.nativeElement.querySelector('app-meet-avatar-picker')).not.toBeNull();
    api.selectAvatar.mockReturnValue(of({ ...source, controls: { ...source.controls, revision: 2 } }));
    c.selectAvatar(source, effective().selection);
    expect(api.selectAvatar).toHaveBeenCalledWith('project', 'task', { expected_revision: 1, profile: effective().selection });
    expect(api.control).not.toHaveBeenCalled(); expect(c.dialogs()[0].controls.avatar?.enabled).toBe(false);
    c.selectAvatar(c.dialogs()[0], null);
    expect(api.selectAvatar).toHaveBeenLastCalledWith('project', 'task', { expected_revision: 2, neutral: true });
  });
  it('never upgrades old dialogs or retries a rejected avatar-selection CAS automatically', () => {
    const c = setup().componentInstance; c.selectAvatar(row(), null); expect(api.selectAvatar).not.toHaveBeenCalled();
    const source = { ...row(), capabilities: ['avatar.publish'], avatar_selection: { mode: 'neutral-ai-v1' as const },
      controls: { ...row().controls, avatar: { enabled: false, revision: 1, since: 1000 } } };
    api.selectAvatar.mockReturnValue(throwError(() => ({ status: 409 }))); c.selectAvatar(source, null);
    expect(api.selectAvatar).toHaveBeenCalledTimes(1); expect(c.message()).toContain('Bitte aktualisieren');
    c.selectAvatar({ ...source, status: 'cancelled' }, null); expect(api.selectAvatar).toHaveBeenCalledTimes(1);
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
    const f = setup(), c = f.componentInstance; c.avatar = true; c.avatarImages = true;
    if (change === 'account') identity.next(null);
    else { f.componentRef.setInput('projectId', 'other'); f.detectChanges(); }
    expect(c.avatar).toBe(false); expect(c.avatarImages).toBe(false); expect(api.start).not.toHaveBeenCalled();
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
  it('retries the same unresolved observable while new starts are locked and independent stop remains available', async () => {
    let subscriptions = 0;
    api.start.mockReturnValue(defer(() => ++subscriptions === 1 ? throwError(() => ({ status: 503 })) : of({ task_id: 'task' })));
    const f = setup(), c = f.componentInstance; c.screen = true; c.start();
    expect(c.startPending()).toBe(true); expect(subscriptions).toBe(1);
    f.detectChanges(); await f.whenStable();
    expect(f.nativeElement.querySelector('fieldset').disabled).toBe(true);
    c.chat = true; c.start(); expect(api.start).toHaveBeenCalledOnce();
    c.reload(); expect(c.startPending()).toBe(true);
    c.stop(row()); expect(api.stop).toHaveBeenCalledOnce(); expect(c.startPending()).toBe(true);
    c.retryStart(); expect(subscriptions).toBe(2); expect(api.start).toHaveBeenCalledOnce();
    expect(c.startPending()).toBe(false); expect(c.dialogs()).toHaveLength(1);
  });
  it.each([409, 503, 0])('keeps an unresolved %s operation until explicit replacement, never silently creating a fresh start', status => {
    api.start.mockReturnValue(throwError(() => ({ status })));
    const c = setup().componentInstance; c.screen = true; c.start(); c.start(); c.retryStart();
    expect(api.start).toHaveBeenCalledOnce(); expect(c.startPending()).toBe(true);
    c.prepareAnotherStart(); expect(api.start).toHaveBeenCalledOnce(); expect(api.stop).not.toHaveBeenCalled();
    expect(c.message()).toContain('ursprüngliche Auftrag kann weiterlaufen');
    c.start(); expect(api.start).toHaveBeenCalledTimes(2);
  });
  it.each(['account', 'project', 'task', 'destroy'])('discards pending starts and ignores late responses after %s change', change => {
    const stream = new Subject(); api.start.mockReturnValue(stream);
    const f = setup(), c = f.componentInstance; c.screen = true; c.start();
    expect(c.startPending()).toBe(true); c.retryStart(); expect(api.start).toHaveBeenCalledOnce();
    if (change === 'account') identity.next(null);
    if (change === 'project') { f.componentRef.setInput('projectId', 'other'); f.detectChanges(); }
    if (change === 'task') { f.componentRef.setInput('taskId', 'other'); f.detectChanges(); }
    if (change === 'destroy') f.destroy();
    stream.next({ task_id: 'late-task' });
    expect(stream.observed).toBe(false); expect(c.startPending()).toBe(false); expect(c.dialogs()).toEqual([]);
    c.retryStart(); expect(api.start).toHaveBeenCalledOnce(); expect(api.stop).not.toHaveBeenCalled();
    expect(api.list).not.toHaveBeenCalled();
  });
  it('does not forget or replace an in-flight operation and does not repeat it on reload failure', () => {
    const stream = new Subject(); api.start.mockReturnValue(stream);
    const c = setup().componentInstance; c.screen = true; c.start(); c.prepareAnotherStart();
    expect(c.startPending()).toBe(true); expect(api.start).toHaveBeenCalledOnce();
    stream.error({ status: 503 }); api.list.mockReturnValue(throwError(() => ({ status: 503 })));
    c.reload(); expect(c.startPending()).toBe(true); expect(api.start).toHaveBeenCalledOnce();
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
