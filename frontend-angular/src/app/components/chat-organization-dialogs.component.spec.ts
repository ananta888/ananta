import { TestBed } from '@angular/core/testing';

import { ChatOrganizationDialogsComponent } from './chat-organization-dialogs.component';
import { ReorganizeProposal } from '../services/chat-sessions.service';

describe('ChatOrganizationDialogsComponent', () => {
  it('marks a persisted proposal draft when an operation is edited', async () => {
    await TestBed.configureTestingModule({ imports: [ChatOrganizationDialogsComponent] }).compileComponents();
    const component = TestBed.createComponent(ChatOrganizationDialogsComponent).componentInstance;
    const proposal: ReorganizeProposal = {
      id: 'proposal-1', status: 'ready', base_state_hash: 'hash', input_policy: 'metadata_only',
      operations: [{ operation_id: 'create', type: 'folder.create', temp_id: 'work', after: { name: 'Work' } }],
      validation_errors: [], folders: [], assignments: {}, summary: 'Proposal',
    };
    component.proposal = proposal;
    component.setFolderName(proposal.operations[0], 'Projects');
    expect(proposal.status).toBe('draft');
    expect(component.folderName(proposal.operations[0])).toBe('Projects');
  });

  it('keeps operation kinds human-readable', () => {
    const component = new ChatOrganizationDialogsComponent();
    expect(component.operationLabel({ operation_id: 'move', type: 'conversation.move' }))
      .toBe('Conversation verschieben');
  });
});
