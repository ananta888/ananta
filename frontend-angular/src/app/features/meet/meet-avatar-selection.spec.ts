import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';
import { MeetAvatarSelection, validateAvatarSelection } from './meet-avatar-selection';

const selection = (): MeetAvatarSelection => ({ mode: 'persona-image-v1',
  reference: { tenant_id: 'tenant', project_id: 'project', artifact_id: 'image', revision: 1,
    sha256: 'a'.repeat(64), kind: 'image', classification: 'test_only' },
  profile: { organization_id: 'org', owner_kind: 'organization', owner_id: 'org', selection_digest: 'b'.repeat(64) } });
const row = () => ({ schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['avatar.publish'], avatar_selection: selection(), controls: { revision: 1,
    ...Object.fromEntries(['chat', 'audio', 'screen', 'avatar'].map(name => [name, { enabled: false, revision: 1, since: 1000 }])) } });

describe('closed content-free avatar selection', () => {
  it('keeps neutral and image modes explicit and cannot put image bytes in status', () => {
    const image = selection(); expect(validateAvatarSelection(image)).toBe(image);
    expect(validateAvatarSelection({ mode: 'neutral-ai-v1' })).toEqual({ mode: 'neutral-ai-v1' });
    for (const value of [null, undefined, {}, { mode: 'neutral-ai-v1', png: 'private' }, { ...image, png: 'private' },
      { ...image, mode: 'https://image' }, { ...image, reference: null }, { ...image, profile: null }]) {
      expect(() => validateAvatarSelection(value as never)).toThrow();
    }
  });
  it('requires strict immutable image metadata and profile pin', () => {
    const image = selection(); if (image.mode !== 'persona-image-v1') throw new Error('fixture');
    for (const patch of [{ revision: true }, { revision: 2 }, { kind: 'voice' }, { tenant_id: '../other' },
      { sha256: 'invalid' }, { classification: 'unverified' }, { permission: 'publish' }]) {
      expect(() => validateAvatarSelection({ ...image, reference: { ...image.reference, ...patch } } as never)).toThrow();
    }
    for (const patch of [{ owner_kind: 'worker' }, { selection_digest: 'invalid' }, { publish: true }]) {
      expect(() => validateAvatarSelection({ ...image, profile: { ...image.profile, ...patch } } as never)).toThrow();
    }
  });
  it('requires avatar capability and assigned control for the additive status field', () => {
    const value = row(); expect(validateDialog(value as never)).toBe(value);
    expect(() => validateDialog({ ...value, capabilities: [] } as never)).toThrow();
    expect(() => validateDialog({ ...value, avatar_selection: undefined } as never)).toThrow();
    expect(() => validateDialog({ ...value, controls: { ...value.controls, avatar: undefined } } as never)).toThrow();
  });
  it('selects through the current Hub CAS endpoint without enabling source controls', () => {
    const core = { request: vi.fn(() => of(row())) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const api = TestBed.inject(MeetDialogApiService), body = { expected_revision: 1, neutral: true };
    let result: unknown; api.selectAvatar('project', 'task', body).subscribe(value => result = value);
    expect(result).toEqual(row());
    expect(core.request).toHaveBeenCalledExactlyOnceWith('PUT', 'https://hub.test/api/meet/v1/projects/project/dialogs/task/avatar',
      'https://hub.test', { body });
  });
});
