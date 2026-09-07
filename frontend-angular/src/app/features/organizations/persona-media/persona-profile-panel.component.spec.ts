import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, Subject, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { PersonaProfileApiClient } from './persona-profile-api.client';
import { PersonaProfilePanelComponent } from './persona-profile-panel.component';
import { PersonaProfileSnapshot } from './persona-profile.models';

const blank: PersonaProfileSnapshot = { profile: null, revision: 0, content_hash: null, media_available: true, tenant_id: 'tenant' };
const image = { tenant_id: 'tenant', project_id: 'project', artifact_id: 'image', revision: 1, sha256: 'a'.repeat(64), kind: 'image', classification: 'test_only' } as const;
const video = { ...image, artifact_id: 'clip', kind: 'video' } as const;
const voice = { ...image, artifact_id: 'voice', kind: 'voice' } as const;

function setup(current: () => Observable<PersonaProfileSnapshot> = () => of(blank)) {
  const state = {
    hubUrl: signal('https://hub.test'), projectId: signal('project'), selectedOrganizationId: signal('org'),
    topology: signal({ organization_id: 'org', nodes: [
      { kind: 'team', team_id: 'team', label: 'Team A' },
      { kind: 'assignment', assignment_id: 'assignment', label: 'Agent A' },
    ] }),
  };
  const api = { current: vi.fn(current), effective: vi.fn(() => of({ purpose: 'preview', runtime_bound: false, topology_revision: 1, media: [] })), save: vi.fn(() => of({ revision: 1, content_hash: 'b'.repeat(64) })),
    image: vi.fn(() => of(image)), preview: vi.fn(() => of(new Blob(['synthetic-png'], { type: 'image/png' }))),
    video: vi.fn(() => of(video)), videoPreview: vi.fn(() => of(new Blob(['synthetic-clip-preview'], { type: 'image/png' }))),
    images: vi.fn(() => of({ items: [image], next_cursor: null as string | null, purpose: 'preview' })),
    videos: vi.fn(() => of({ items: [video], next_cursor: null as string | null, purpose: 'preview' })),
    voice: vi.fn(() => of(voice)),
    voices: vi.fn(() => of({ items: [voice], next_cursor: null as string | null, purpose: 'preview' })),
  };
  TestBed.configureTestingModule({ providers: [
    { provide: OrganizationTopologyStateService, useValue: state }, { provide: PersonaProfileApiClient, useValue: api },
  ] });
  const fixture = TestBed.createComponent(PersonaProfilePanelComponent);
  fixture.detectChanges();
  return { fixture, state, api, facade: fixture.componentInstance.facade };
}

describe('Persona profile panel', () => {
  it('checks and saves an explicit voice reference without playing audio or changing other media', () => {
    const { fixture, facade, api } = setup(); facade.personaId.set('presentation');
    facade.selectVoiceState('asset'); facade.changeVoiceId('voice'); facade.save();
    expect(api.save).not.toHaveBeenCalled(); expect(facade.error()).toContain('Stimm-ID prüfen');
    facade.listVoices(); expect(facade.voiceOptions()).toEqual([voice]);
    facade.chooseListedVoice('not-listed'); expect(api.voice).not.toHaveBeenCalled();
    facade.chooseListedVoice('voice'); expect(api.voice).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ project: 'project' }), 'voice');
    expect(api.preview).not.toHaveBeenCalled(); expect(api.videoPreview).not.toHaveBeenCalled();
    fixture.detectChanges(); expect(fixture.nativeElement.querySelector('app-persona-voice-picker')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('audio,video')).toBeNull();
    facade.save(); expect(api.save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      voice: { state: 'asset', asset: voice }, image: { state: 'missing', asset: null }, video: { state: 'missing', asset: null },
    }), 0);
  });

  it.each(['inherit', 'disabled'] as const)('saves explicit voice %s without reading or synthesizing an asset', state => {
    const { facade, api } = setup(); facade.personaId.set('presentation'); facade.selectVoiceState(state); facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ voice: { state, asset: null } }), 0);
    expect(api.voice).not.toHaveBeenCalled(); expect(api.voices).not.toHaveBeenCalled();
  });

  it('preserves an existing voice pin when only an image state is edited', () => {
    const empty = { state: 'missing' as const, asset: null };
    const { facade, api } = setup(() => of({ ...blank, revision: 2, profile: {
      schema_version: 'ananta.persona-media.v1', tenant_id: 'tenant', project_id: 'project', owner_kind: 'organization',
      owner_id: 'org', persona_id: 'presentation', revision: 2, image: empty, video: empty, style: empty,
      voice: { state: 'asset', asset: voice }, requested_usage: ['preview'],
    } }));
    expect(facade.voice()).toEqual(voice); facade.selectImageState('disabled'); facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      image: { state: 'disabled', asset: null }, voice: { state: 'asset', asset: voice }, requested_usage: ['preview'],
    }), 2);
    expect(api.voice).not.toHaveBeenCalled();
  });

  it('replaces voice pages and clears old references on owner and project changes', () => {
    const { fixture, facade, api, state } = setup();
    api.voices.mockReturnValue(of({ items: [voice], next_cursor: 'v'.repeat(43), purpose: 'preview' })); facade.listVoices();
    api.voices.mockReturnValue(of({ items: [], next_cursor: null, purpose: 'preview' })); facade.listVoices(true);
    expect(api.voices).toHaveBeenLastCalledWith(expect.anything(), 'v'.repeat(43)); expect(facade.voiceOptions()).toEqual([]);
    facade.changeVoiceId('voice'); facade.inspectVoice(); expect(facade.voice()).toEqual(voice);
    facade.chooseOwner('team', 'team'); expect(facade.voice()).toBeNull(); expect(facade.voicesLoaded()).toBe(false);
    state.projectId.set('other'); facade.inspectVoice(); facade.listVoices(); fixture.detectChanges();
    expect(facade.voice()).toBeNull(); expect(facade.voiceCursor()).toBeNull();
  });

  it('discards late voice references and never saves after a rejected check', () => {
    const { fixture, facade, api, state } = setup(); const pending = new Subject<typeof voice>();
    api.voice.mockReturnValue(pending); facade.selectVoiceState('asset'); facade.changeVoiceId('voice'); facade.inspectVoice();
    state.projectId.set('other'); pending.next(voice); expect(facade.voice()).toBeNull(); fixture.detectChanges();
    api.voice.mockReturnValue(throwError(() => ({ status: 403 }))); facade.personaId.set('presentation');
    facade.selectVoiceState('asset'); facade.changeVoiceId('voice'); facade.inspectVoice(); facade.save();
    expect(api.save).not.toHaveBeenCalled(); expect(facade.voice()).toBeNull();
  });

  it('bounds a voice metadata request without waiting for a person or automatically retrying', () => {
    const { facade, api } = setup(); const pending = new Subject<typeof voice>();
    vi.useFakeTimers();
    try {
      api.voice.mockReturnValue(pending); facade.selectVoiceState('asset'); facade.changeVoiceId('voice'); facade.inspectVoice();
      expect(facade.busy()).toBe(true); vi.advanceTimersByTime(10_001);
      expect(facade.busy()).toBe(false); expect(facade.voice()).toBeNull(); expect(facade.error()).toContain('Nicht verfügbar');
      expect(api.voice).toHaveBeenCalledTimes(1); pending.next(voice); expect(facade.voice()).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it('saves an explicit inherited selection without publishing or inventing an asset', () => {
    const { fixture, facade, api } = setup();
    facade.personaId.set('presentation');
    facade.selectImageState('inherit');
    facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ organization: 'org', owner: 'org' }),
      expect.objectContaining({ revision: 1, persona_id: 'presentation', image: { state: 'inherit', asset: null }, requested_usage: [] }), 0);
    expect(api.preview).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('weder ein Meet-Raum');
    expect(fixture.nativeElement.textContent).toContain('Fallback stoppen');
  });

  it('maps agent profiles to logical assignments, never worker URLs', () => {
    const { fixture, api } = setup();
    fixture.componentInstance.choose('agent:assignment');
    expect(api.current).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'agent', owner: 'assignment' }));
    fixture.componentInstance.choose('agent:http://worker.test');
    expect(api.current).toHaveBeenCalledTimes(2);
  });

  it('cancels old scope reads and refuses saves even before the scope effect flushes', () => {
    const pending = new Subject<PersonaProfileSnapshot>();
    const { fixture, facade, api, state } = setup(() => pending);
    pending.next(blank);
    facade.personaId.set('presentation');
    state.projectId.set('other');
    facade.save();
    expect(api.save).not.toHaveBeenCalled();
    pending.next({ ...blank, revision: 99 });
    expect(facade.snapshot()?.revision).toBe(0);
    fixture.detectChanges();
    expect(facade.snapshot()).toBeNull();
    expect(api.current).toHaveBeenLastCalledWith(expect.objectContaining({ project: 'other' }));
  });

  it('cleans up authenticated preview object URLs when selection or scope changes', () => {
    const create = vi.fn(() => 'blob:synthetic-preview');
    const revoke = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revoke });
    const { fixture, facade, state } = setup();
    facade.selectImageState('asset');
    facade.changeImageId('image');
    facade.inspectImage();
    expect(facade.previewUrl()).toBe('blob:synthetic-preview');
    state.selectedOrganizationId.set('different');
    fixture.detectChanges();
    expect(revoke).toHaveBeenCalledWith('blob:synthetic-preview');
    expect(facade.image()).toBeNull();
    expect(facade.previewUrl()).toBe('');
  });

  it('keeps a revoked profile repairable and reports conflicts without an automatic retry', () => {
    const { facade, api } = setup(() => of({ ...blank, revision: 7, media_available: false }));
    facade.personaId.set('replacement');
    facade.selectImageState('disabled');
    api.save.mockImplementation(() => throwError(() => new Error('synthetic-conflict')));
    facade.save();
    expect(api.save).toHaveBeenCalledOnce();
    expect(api.save.mock.calls[0][2]).toBe(7);
    expect(facade.error()).toContain('Revisionskonflikt');
    expect(facade.busy()).toBe(false);
  });

  it('requires an inspected reference before saving an explicit asset', () => {
    const { facade, api } = setup();
    facade.personaId.set('presentation');
    facade.selectImageState('asset');
    facade.changeImageId('unverified-id');
    facade.save();
    expect(api.save).not.toHaveBeenCalled();
    expect(facade.error()).toContain('zuerst die Bild-ID prüfen');
  });

  it('shows effective inheritance provenance without overwriting the explicit selection', () => {
    const { fixture, facade, api } = setup();
    facade.selectImageState('inherit');
    facade.effective.set({ purpose: 'preview', runtime_bound: false, topology_revision: 3,
      selection: { organization_id: 'org', owner_kind: 'organization', owner_id: 'org', selection_digest: 'a'.repeat(64) }, media: [{
      kind: 'image', state: 'asset', asset: image, available: true, preview_allowed: true, publication_checked: false,
      origins: [{ owner_kind: 'organization', owner_id: 'org', persona_id: 'org-presentation', profile_revision: 5, selection_state: 'asset' }],
    }] });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-inherited' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('org-presentation');
    expect(fixture.nativeElement.textContent).toContain('Revision 5');
    expect(fixture.nativeElement.textContent).toContain('test_only');
    facade.previewEffective();
    expect(api.preview).toHaveBeenCalledOnce();
    expect(facade.imageState()).toBe('inherit');
    expect(facade.image()).toBeNull();
    expect(api.save).not.toHaveBeenCalled();
  });

  it('lists permitted images and rechecks selection without publishing', () => {
    const { fixture, facade, api } = setup();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-listed' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    facade.selectImageState('asset');
    facade.listImages();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Für deine Vorschau freigegebene Bilder');
    expect(facade.imageOptions()).toEqual([image]);
    facade.chooseListedImage('unlisted');
    expect(api.image).not.toHaveBeenCalled();
    facade.chooseListedImage('image');
    expect(api.image).toHaveBeenCalledOnce();
    expect(api.preview).toHaveBeenCalledOnce();
    expect(api.save).not.toHaveBeenCalled();
  });

  it('replaces result pages and clears private list/cursor state on project change', () => {
    const { fixture, facade, api, state } = setup();
    api.images.mockImplementation(() => of({ items: [image], next_cursor: 'opaque-next', purpose: 'preview' }));
    facade.listImages();
    facade.listImages(true);
    expect(api.images).toHaveBeenLastCalledWith(expect.objectContaining({ project: 'project' }), 'opaque-next');
    expect(facade.imageOptions()).toHaveLength(1);
    state.projectId.set('other');
    fixture.detectChanges();
    expect(facade.imageOptions()).toEqual([]);
    expect(facade.imageCursor()).toBeNull();
  });

  it('selects an admitted clip independently of the image and saves its exact reference', () => {
    const { fixture, facade, api } = setup();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-clip' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    facade.personaId.set('presentation');
    facade.selectImageState('inherit');
    facade.selectVideoState('asset');
    facade.changeVideoId('clip');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Zugelassene Video-ID');
    facade.inspectVideo();
    expect(api.video).toHaveBeenCalledWith(expect.objectContaining({ project: 'project' }), 'clip');
    expect(api.videoPreview).toHaveBeenCalledOnce();
    expect(facade.videoPreviewUrl()).toBe('blob:synthetic-clip');
    expect(api.image).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('video')).toBeNull();
    facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      image: { state: 'inherit', asset: null }, video: { state: 'asset', asset: video },
    }), 0);
  });

  it('lists clips and rechecks the selected reference before loading only its PNG preview', () => {
    const { fixture, facade, api } = setup();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:listed-clip' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    facade.selectVideoState('asset');
    facade.listVideos();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Für deine Vorschau freigegebene Clips');
    expect(facade.videoOptions()).toEqual([video]);
    facade.chooseListedVideo('unlisted');
    expect(api.video).not.toHaveBeenCalled();
    facade.chooseListedVideo('clip');
    expect(api.video).toHaveBeenCalledOnce();
    expect(api.videoPreview).toHaveBeenCalledOnce();
    expect(api.image).not.toHaveBeenCalled();
    expect(api.save).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('video')).toBeNull();
  });

  it('replaces clip pages and discards private cursor state on project changes', () => {
    const { fixture, facade, api, state } = setup();
    api.videos.mockImplementation(() => of({ items: [video], next_cursor: 'v'.repeat(43), purpose: 'preview' }));
    facade.listVideos();
    facade.listVideos(true);
    expect(api.videos).toHaveBeenLastCalledWith(expect.objectContaining({ project: 'project' }), 'v'.repeat(43));
    expect(facade.videoOptions()).toHaveLength(1);
    state.projectId.set('other');
    facade.chooseListedVideo('clip');
    expect(api.video).not.toHaveBeenCalled();
    fixture.detectChanges();
    expect(facade.videoOptions()).toEqual([]);
    expect(facade.videoCursor()).toBeNull();
    expect(facade.videosLoaded()).toBe(false);
  });

  it('drops late private clip pages after owner switches', () => {
    const { facade, api } = setup();
    const pending = new Subject<{ items: typeof video[]; next_cursor: string | null; purpose: string }>();
    api.videos.mockImplementation(() => pending);
    facade.listVideos();
    facade.chooseOwner('team', 'team');
    pending.next({ items: [video], next_cursor: 'v'.repeat(43), purpose: 'preview' });
    expect(facade.videoOptions()).toEqual([]);
    expect(facade.videoCursor()).toBeNull();
  });

  it.each(['inherit', 'disabled', 'missing'] as const)('saves explicit video state %s without any media read', state => {
    const { facade, api } = setup();
    facade.personaId.set('presentation');
    facade.selectVideoState(state);
    facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ video: { state, asset: null } }), 0);
    expect(api.video).not.toHaveBeenCalled();
    expect(api.videoPreview).not.toHaveBeenCalled();
  });

  it('requires a checked video reference and invalidates it when its ID changes', () => {
    const { facade, api } = setup();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-clip' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    facade.personaId.set('presentation');
    facade.selectVideoState('asset');
    facade.changeVideoId('clip');
    facade.inspectVideo();
    facade.changeVideoId('unchecked');
    facade.save();
    expect(facade.video()).toBeNull();
    expect(facade.error()).toContain('zuerst die Video-ID prüfen');
    expect(api.save).not.toHaveBeenCalled();
  });

  it('drops late clip responses after owner change and clears private preview URLs', () => {
    const { facade, api } = setup();
    const pending = new Subject<Blob>();
    api.videoPreview.mockImplementation(() => pending);
    const create = vi.fn(() => 'blob:must-not-exist');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    facade.selectVideoState('asset');
    facade.changeVideoId('clip');
    facade.inspectVideo();
    facade.chooseOwner('team', 'team');
    pending.next(new Blob(['late-private-preview'], { type: 'image/png' }));
    expect(create).not.toHaveBeenCalled();
    expect(facade.video()).toBeNull();
    expect(facade.videoPreviewUrl()).toBe('');
  });

  it('revokes a loaded clip preview on scope change and on destruction', () => {
    const { fixture, facade, state } = setup();
    const revoke = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-clip' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revoke });
    facade.selectVideoState('asset');
    facade.changeVideoId('clip');
    facade.inspectVideo();
    state.projectId.set('other');
    fixture.detectChanges();
    expect(revoke).toHaveBeenCalledWith('blob:synthetic-clip');
    expect(facade.video()).toBeNull();
    facade.selectVideoState('asset');
    facade.changeVideoId('clip');
    facade.inspectVideo();
    fixture.destroy();
    expect(revoke).toHaveBeenCalledTimes(2);
  });
});
