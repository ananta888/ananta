import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { UserAuthService } from '../../services/user-auth.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import { effective } from './meet-avatar-test-fixtures';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { MeetDialogComponent } from './meet-dialog.component';
import { MeetInitialPersonaComponent } from './meet-initial-persona.component';

const pin = () => effective().selection;

describe('passive initial persona composition', () => {
  const profiles = { effective: vi.fn() };
  beforeEach(() => {
    profiles.effective.mockReset();
    TestBed.configureTestingModule({ imports: [MeetInitialPersonaComponent], providers: [
      { provide: PersonaProfileApiClient, useValue: profiles },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } },
    ] });
  });
  function setup() {
    const fixture = TestBed.createComponent(MeetInitialPersonaComponent);
    fixture.componentRef.setInput('projectId', 'p');
    for (const flag of ['images', 'videos', 'voices']) fixture.componentRef.setInput(flag, true);
    fixture.detectChanges();
    return { fixture, c: fixture.componentInstance, emit: vi.spyOn(fixture.componentInstance.choiceChanged, 'emit') };
  }
  it('starts empty without query or playback and copies independently selected output pins', () => {
    const { fixture, c, emit } = setup(), profile = pin();
    expect(profiles.effective).not.toHaveBeenCalled(); expect(c.value()).toEqual({});
    c.image(profile); c.voice(profile);
    expect(c.value()).toEqual({ avatar: { mode: 'persona-image-v1', profile }, voice: { profile } });
    const sent = emit.mock.lastCall![0]!;
    sent.voice!.profile.owner_id = 'foreign'; profile.owner_id = 'changed';
    expect(c.value().voice!.profile).toEqual(pin());
    c.video({ profile: pin(), repeat_mode: 'hold_last' });
    expect(c.value().avatar?.mode).toBe('persona-video-v1'); expect(c.value().voice!.profile).toEqual(pin());
    c.image(null); expect(c.value()).toEqual({ voice: { profile: pin() } });
    c.voice(null); expect(emit.mock.lastCall![0]).toBeNull();
    expect(fixture.nativeElement.querySelectorAll('img,video,audio')).toHaveLength(0);
  });
  it.each(['projectId', 'taskId'])('clears all prior pins when %s changes', field => {
    const { fixture, c, emit } = setup(); c.image(pin()); c.voice(pin());
    fixture.componentRef.setInput(field, 'new-scope'); fixture.detectChanges();
    expect(c.value()).toEqual({}); expect(emit.mock.lastCall![0]).toBeNull();
  });
  it('withdraws only the disabled output and rejects further disabled choices', () => {
    const { fixture, c, emit } = setup(); c.video({ profile: pin(), repeat_mode: 'loop' }); c.voice(pin());
    fixture.componentRef.setInput('videos', false); fixture.detectChanges();
    expect(c.value()).toEqual({ voice: { profile: pin() } });
    c.video({ profile: pin(), repeat_mode: 'loop' }); expect(c.value().avatar).toBeUndefined();
    c.image(pin()); fixture.componentRef.setInput('voices', false); fixture.detectChanges();
    expect(c.value()).toEqual({ avatar: { mode: 'persona-image-v1', profile: pin() } });
    fixture.componentRef.setInput('disabled', true); fixture.detectChanges(); emit.mockClear();
    c.image(null); c.voice(pin()); c.video({ profile: pin(), repeat_mode: 'loop' }); expect(emit).not.toHaveBeenCalled();
  });
});

describe('initial persona start command', () => {
  function setup() {
    const api = { start: vi.fn<MeetDialogApiService['start']>(() => of({
      schema: 'ananta.meet-dialog-start.v1', task_id: 'task', session_id: 'session', status: 'connecting',
    })), list: vi.fn(() => of({ items: [], next_cursor: null })) };
    TestBed.configureTestingModule({ providers: [{ provide: MeetDialogApiService, useValue: api },
      { provide: UserAuthService, useValue: { user$: of({ id: 'owner' }) } }] });
    const c = TestBed.runInInjectionContext(() => new MeetDialogComponent()); c.projectId = 'p'; c.taskId = 'parent';
    c.setAvatar(true); c.setAvatarImages(true); c.setAvatarVideos(true); c.setChat(true); c.setSpeech(true); c.setVoiceProfiles(true);
    c.initialPersona = { avatar: { mode: 'persona-video-v1', profile: pin(), repeat_mode: 'loop' }, voice: { profile: pin() } };
    return { c, api };
  }
  it('sends a copied initial pin without control commands or mutable retry input', () => {
    const { c, api } = setup(); const original = structuredClone(c.initialPersona);
    c.start(); expect(api.start.mock.calls[0][2]).toMatchObject({ initial_persona: original, avatar_videos: true, voice_profiles: true });
    c.initialPersona!.voice!.profile.owner_id = 'changed';
    expect(api.start.mock.calls[0][2]).toMatchObject({ initial_persona: original }); c.ngOnDestroy();
  });
  it('clears disabled pins synchronously and never silently upgrades a mismatched choice', () => {
    const { c, api } = setup(); c.avatarVideos = false; c.start(); expect(api.start).not.toHaveBeenCalled();
    c.setAvatarVideos(false); expect(c.initialPersona?.avatar).toBeUndefined(); expect(c.initialPersona?.voice).toBeDefined();
    c.setChat(false); expect(c.initialPersona).toBeNull(); c.ngOnChanges(); expect(c.initialPersona).toBeNull(); c.ngOnDestroy();
  });
});
