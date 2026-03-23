import { AiAssistantMessageListComponent } from './ai-assistant-message-list.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';

describe('AiAssistantMessageListComponent', () => {
  function createComponent(): AiAssistantMessageListComponent & { [key: string]: any } {
    const cmp = new AiAssistantMessageListComponent(new AiAssistantDomainService()) as AiAssistantMessageListComponent & {
      [key: string]: any;
    };
    cmp.chatHistory = [];
    cmp.busy = false;
    return cmp;
  }

  it('sanitizes rendered markdown output', () => {
    const cmp = createComponent();

    const html = cmp.renderMarkdown('**bold** <script>alert(1)</script>');

    expect(html).toContain('<strong>bold</strong>');
    expect(html).not.toContain('<script>');
  });

  it('delegates tool summaries to the domain service', () => {
    const cmp = createComponent();

    expect(cmp.formatToolName('update_config')).toBe('Update Configuration');
    expect(cmp.summarizeToolScope({ name: 'create_team', args: { name: 'Platform', team_type: 'scrum' } })).toContain('Platform');
    expect(cmp.summarizeToolChanges({ name: 'update_config', args: { key: 'http_timeout', value: 30 } })).toContain('config.http_timeout');
  });

  it('clears sgpt commands on demand', () => {
    const cmp = createComponent();
    const msg: any = { role: 'assistant', content: 'run this', sgptCommand: 'ls -la' };

    cmp.clearSgptCommand(msg);

    expect(msg.sgptCommand).toBeUndefined();
  });

  it('scrolls chat history to the bottom after view checks', () => {
    const cmp = createComponent();
    const nativeElement = { scrollTop: 0, scrollHeight: 480 };
    cmp['chatBox'] = { nativeElement };

    cmp.ngAfterViewChecked();

    expect(nativeElement.scrollTop).toBe(480);
  });
});
