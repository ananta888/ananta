import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import {
  ChatSessionsService,
  normalizeEffectiveChatProcessSource,
  ReorganizeProposal,
} from './chat-sessions.service';
import { HubApiCoreService } from './hub-api-core.service';

describe('ChatSessionsService organization workflow', () => {
  const proposal: ReorganizeProposal = {
    id: 'proposal-1',
    status: 'ready',
    base_state_hash: 'base',
    input_policy: 'metadata_only',
    operations: [],
    validation_errors: [],
    folders: [],
    assignments: {},
    summary: 'Proposal',
    method: 'heuristic',
  };

  const core = {
    get: vi.fn(() => of([])),
    post: vi.fn(() => of(proposal)),
    patch: vi.fn(() => of(proposal)),
    delete: vi.fn(() => of(undefined)),
  };
  const directory = { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub' }] };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        ChatSessionsService,
        { provide: HubApiCoreService, useValue: core },
        { provide: AgentDirectoryService, useValue: directory },
      ],
    });
  });

  it('sends the explicit privacy policy when requesting AI organization', () => {
    const service = TestBed.inject(ChatSessionsService);
    service.aiReorganize('metadata_plus_preview').subscribe();
    expect(core.post).toHaveBeenCalledWith(
      'http://hub/api/chat/sessions/ai-reorganize',
      { input_policy: 'metadata_plus_preview' },
      'http://hub',
    );
  });

  it('uses persisted validate and atomic apply endpoints', () => {
    const service = TestBed.inject(ChatSessionsService);
    service.updateProposal('proposal-1', { operations: [] }).subscribe();
    service.validateProposal('proposal-1').subscribe();
    service.applyProposal('proposal-1').subscribe();

    expect(core.patch).toHaveBeenCalledWith(
      'http://hub/api/chat/organization/proposals/proposal-1', { operations: [] }, 'http://hub',
    );
    expect(core.post).toHaveBeenCalledWith(
      'http://hub/api/chat/organization/proposals/proposal-1/validate', {}, 'http://hub',
    );
    expect(core.post).toHaveBeenCalledWith(
      'http://hub/api/chat/organization/proposals/proposal-1/apply', {}, 'http://hub',
    );
  });

  it('loads history and calls the revision revert endpoint', () => {
    const service = TestBed.inject(ChatSessionsService);
    service.loadOrganizationHistory().subscribe();
    service.revertRevision('revision-1').subscribe();
    expect(core.get).toHaveBeenCalledWith('http://hub/api/chat/organization/history', 'http://hub');
    expect(core.post).toHaveBeenCalledWith(
      'http://hub/api/chat/organization/history/revision-1/revert', {}, 'http://hub',
    );
  });
});

describe('effective process source compatibility', () => {
  it('maps legacy values onto the shared backend enum', () => {
    expect(normalizeEffectiveChatProcessSource('session')).toBe('session_override');
    expect(normalizeEffectiveChatProcessSource('session_override')).toBe('session_override');
    expect(normalizeEffectiveChatProcessSource('profile')).toBe('profile');
    expect(normalizeEffectiveChatProcessSource('none')).toBe('global');
    expect(normalizeEffectiveChatProcessSource('unexpected')).toBe('global');
  });
});
