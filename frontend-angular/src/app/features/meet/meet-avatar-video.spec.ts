import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { effective, scope } from './meet-avatar-test-fixtures';
import { avatarVideoCandidate, validateAvatarVideoSelection } from './meet-avatar-video-selection';
import { MeetAvatarVideoPickerComponent } from './meet-avatar-video-picker.component';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetDialogComponent } from './meet-dialog.component';
import { UserAuthService } from '../../services/user-auth.service';

function effectiveVideo(): PersonaEffectiveProfile {
  const value = effective();
  value.media = value.media.map(row => row.kind === 'video' ? { ...row, state: 'asset', available: true, preview_allowed: true,
    asset: { tenant_id: 't', project_id: 'p', artifact_id: 'clip', revision: 1, sha256: 'c'.repeat(64), kind: 'video', classification: 'test_only' } } : row);
  return value;
}
const selected = () => avatarVideoCandidate(effectiveVideo(), scope);
const row = () => ({ schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['avatar.publish'], avatar_videos: true as const, avatar_selection: selected(), controls: { revision: 1,
    chat: { enabled: false, revision: 1, since: 1000 }, audio: { enabled: false, revision: 1, since: 1000 },
    screen: { enabled: false, revision: 1, since: 1000 }, avatar: { enabled: false, revision: 1, since: 1000 } } });

describe('explicit passive video selection', () => {
  it('accepts an independently authorized video candidate with disabled voice and no playback', () => {
    expect(selected().reference.kind).toBe('video'); expect(selected().repeat_mode).toBe('hold_last');
    expect(validateDialog(row()).avatar_videos).toBe(true);
    for (const patch of [{ avatar_videos: 1 }, { avatar_videos: false }, { avatar_videos: undefined }, { avatar_selection: null }]) {
      expect(() => validateDialog({ ...row(), ...patch } as never)).toThrow();
    }
    for (const patch of [{ repeat_mode: true }, { url: 'https://foreign' }, { reference: { ...selected().reference, kind: 'image' } }]) {
      expect(() => validateAvatarVideoSelection({ ...selected(), ...patch } as never)).toThrow();
    }
    const value = effectiveVideo(); value.media.find(item => item.kind === 'video')!.preview_allowed = false;
    expect(() => avatarVideoCandidate(value, scope)).toThrow();
    expect(() => avatarVideoCandidate(effectiveVideo(), { ...scope, project: 'foreign' })).toThrow();
  });
  const api = { effective: vi.fn() }, directory = { list: vi.fn() };
  beforeEach(() => {
    api.effective.mockReset().mockReturnValue(of(effectiveVideo()));
    directory.list.mockReset().mockReturnValue([{ role: 'hub', url: scope.hub }]);
    TestBed.configureTestingModule({ imports: [MeetAvatarVideoPickerComponent], providers: [
      { provide: PersonaProfileApiClient, useValue: api }, { provide: AgentDirectoryService, useValue: directory },
    ] });
  });
  function setup() {
    const fixture = TestBed.createComponent(MeetAvatarVideoPickerComponent);
    fixture.componentRef.setInput('projectId', 'p'); fixture.detectChanges();
    const component = fixture.componentInstance, emit = vi.spyOn(component.videoSelected, 'emit');
    return { fixture, component, emit };
  }
  it('requires explicit metadata query and choice, emits only copied profile and repeat policy', () => {
    const { fixture, component: c, emit } = setup();
    expect(api.effective).not.toHaveBeenCalled(); c.select(); expect(emit).not.toHaveBeenCalled();
    c.edit('organization', 'org'); c.resolve(); expect(api.effective).toHaveBeenCalledWith(scope);
    c.repeatMode = 'loop'; c.select();
    expect(emit).toHaveBeenCalledExactlyOnceWith({ profile: effectiveVideo().selection, repeat_mode: 'loop' });
    expect(emit.mock.calls[0][0]!.profile).not.toBe(c.candidate()!.profile);
    expect(fixture.nativeElement.querySelectorAll('img,video,audio')).toHaveLength(0);
  });
  it.each(['project', 'disabled', 'owner', 'hub', 'destroy'])('discards stale candidate after %s change', change => {
    const { fixture, component: c, emit } = setup(), pending = new Subject<PersonaEffectiveProfile>();
    api.effective.mockReturnValue(pending); c.organization = 'org'; c.resolve();
    if (change === 'project') { fixture.componentRef.setInput('projectId', 'other'); fixture.detectChanges(); }
    else if (change === 'disabled') { fixture.componentRef.setInput('disabled', true); fixture.detectChanges(); }
    else if (change === 'owner') c.chooseKind('team');
    else if (change === 'hub') directory.list.mockReturnValue([{ role: 'hub', url: 'https://other.test' }]);
    else fixture.destroy();
    pending.next(effectiveVideo()); c.select(); expect(emit).not.toHaveBeenCalled(); expect(c.candidate()).toBeNull();
  });
  it('reports policy failure without selecting any fallback', () => {
    const { component: c, emit } = setup(); c.organization = 'org';
    api.effective.mockReturnValue(throwError(() => ({ status: 403 }))); c.resolve();
    expect(c.message()).toContain('Keine Ersatzquelle'); expect(emit).not.toHaveBeenCalled();
  });
});

describe('video-specific dialog commands', () => {
  it('preserves pause and existing sources, includes revision and never upgrades old assignments', () => {
    const api = { selectAvatarVideo: vi.fn(() => of(row())), start: vi.fn(() => of({ task_id: 'task' })),
      list: vi.fn(() => of({ items: [], next_cursor: null })) };
    TestBed.configureTestingModule({ providers: [{ provide: MeetDialogApiService, useValue: api },
      { provide: UserAuthService, useValue: { user$: of({ id: 'owner' }) } }] });
    const c = TestBed.runInInjectionContext(() => new MeetDialogComponent()); c.projectId = 'p'; c.dialogs.set([row()]);
    const choice = { profile: selected().profile, repeat_mode: 'hold_last' as const };
    c.selectAvatarVideo({ ...row(), avatar_videos: undefined }, choice); expect(api.selectAvatarVideo).not.toHaveBeenCalled();
    c.selectAvatarVideo(row(), choice); expect(api.selectAvatarVideo).toHaveBeenCalledWith('p', 'task', { expected_revision: 1, ...choice });
    expect(c.dialogs()[0].controls.avatar?.enabled).toBe(false);
    expect(c.avatarVideos).toBe(false); c.setAvatar(true); c.avatarVideos = true; c.start(); expect(api.start).not.toHaveBeenCalled();
    c.avatarImages = true; c.start(); expect(api.start).toHaveBeenCalledWith('p', '', { capabilities: ['avatar.publish'],
      duration_seconds: 900, chat_mode: 'off', audio_mode: 'off', avatar_images: true, avatar_videos: true });
    c.setAvatarImages(false); expect(c.avatarVideos).toBe(false);
    c.setAvatarImages(true); expect(c.avatarVideos).toBe(false);
    c.avatarVideos = true; c.setAvatar(false); expect(c.avatarVideos).toBe(false); c.ngOnDestroy();
  });
});
