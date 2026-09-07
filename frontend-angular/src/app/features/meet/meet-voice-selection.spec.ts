import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { scope } from './meet-avatar-test-fixtures';
import { voiceCandidate } from './meet-voice-candidate';
import { validateVoiceSelection } from './meet-voice-selection';
import { effectiveVoice } from './meet-voice-test-fixtures';

const selection = () => voiceCandidate(effectiveVoice(), scope);
const row = () => ({ schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['speech.publish'], voice_selection: selection(), controls: { revision: 1,
    ...Object.fromEntries(['chat', 'audio', 'screen', 'speech'].map(name => [name, { enabled: false, revision: 1, since: 1000 }])) } });

describe('closed independent voice metadata', () => {
  it('accepts explicit configured mode or a pinned voice, never model paths or bytes', () => {
    const voice = selection(); expect(validateVoiceSelection(voice)).toBe(voice);
    expect(validateVoiceSelection({ mode: 'configured-piper-v1' })).toEqual({ mode: 'configured-piper-v1' });
    for (const value of [null, {}, { mode: 'configured-piper-v1', model: '/models/local' }, { ...voice, audio: 'private' },
      { ...voice, mode: 'neutral-ai-v1' }, { ...voice, reference: null }, { ...voice, profile: null }]) {
      expect(() => validateVoiceSelection(value as never)).toThrow();
    }
    if (voice.mode !== 'persona-voice-v1') throw new Error('fixture');
    for (const patch of [{ revision: true }, { revision: 2 }, { kind: 'image' }, { tenant_id: '../other' },
      { sha256: 'invalid' }, { classification: 'unverified' }, { publish: true }]) {
      expect(() => validateVoiceSelection({ ...voice, reference: { ...voice.reference, ...patch } } as never)).toThrow();
    }
    for (const patch of [{ owner_kind: 'worker' }, { selection_digest: 'invalid' }, { publish: true }]) {
      expect(() => validateVoiceSelection({ ...voice, profile: { ...voice.profile, ...patch } } as never)).toThrow();
    }
  });
  it('resolves voice independently from disabled image/video and copies metadata', () => {
    const value = effectiveVoice(), candidate = voiceCandidate(value, scope);
    if (candidate.mode !== 'persona-voice-v1') throw new Error('fixture');
    expect(candidate.reference.kind).toBe('voice'); expect(candidate.profile).not.toBe(value.selection);
    expect(candidate.reference).not.toBe(value.media.find(row => row.kind === 'voice')!.asset);
    for (const patch of [{ state: 'disabled' }, { available: false }, { preview_allowed: false }, { publication_checked: true }, { asset: null }]) {
      expect(() => voiceCandidate({ ...value, media: value.media.map(row => row.kind === 'voice' ? { ...row, ...patch } : row) } as never, scope)).toThrow();
    }
    for (const patch of [{ purpose: 'publish' }, { runtime_bound: true }, { media: [] }, { selection: { ...value.selection, owner_id: 'other' } }]) {
      expect(() => voiceCandidate({ ...value, ...patch } as never, scope)).toThrow();
    }
    expect(() => voiceCandidate(value, { ...scope, project: 'other' })).toThrow();
  });
  it('requires speech capability and control before accepting the additive status field', () => {
    const value = row(); expect(validateDialog(value as never)).toBe(value);
    expect(() => validateDialog({ ...value, capabilities: [] } as never)).toThrow();
    expect(() => validateDialog({ ...value, voice_selection: undefined } as never)).toThrow();
    expect(() => validateDialog({ ...value, controls: { ...value.controls, speech: undefined } } as never)).toThrow();
  });
  it('sends only the selection CAS to the current authenticated Hub', () => {
    const core = { request: vi.fn(() => of(row())) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: scope.hub }] } }] });
    const body = { expected_revision: 1, configured: true }; let result: unknown;
    TestBed.inject(MeetDialogApiService).selectVoice('p', 'task', body).subscribe(value => result = value);
    expect(result).toEqual(row());
    expect(core.request).toHaveBeenCalledExactlyOnceWith('PUT', 'https://hub.test/api/meet/v1/projects/p/dialogs/task/voice', scope.hub, { body });
  });
});
