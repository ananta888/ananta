import { of, throwError } from 'rxjs';
import { SourceChatPanelComponent } from './source-chat-panel.component';

describe('SourceChatPanelComponent', () => {
  it('sends options and renders source references in its response model', () => {
    const chat: any = {
      ask: vi.fn(() => of({
        answer: 'Grounded answer',
        context_hash: 'hash',
        source_references: [{ chunk_id: 'chunk-1', title: 'Source' }],
      })),
    };
    const component = new SourceChatPanelComponent(chat);
    component.sourceId = 'open-notebook-1';
    component.prompt = 'Question';
    component.includeNotes = true;
    component.send();
    expect(chat.ask).toHaveBeenCalledWith('open-notebook-1', 'Question', true, true);
    expect(component.response.source_references).toHaveLength(1);
  });

  it('handles empty prompts and backend errors', () => {
    const chat: any = { ask: () => throwError(() => new Error('disabled')) };
    const component = new SourceChatPanelComponent(chat);
    component.send();
    expect(component.error).toBe('prompt_required');
    component.prompt = 'Question';
    component.send();
    expect(component.error).toContain('disabled');
  });
});
