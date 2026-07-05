import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SourceChatService } from '../../services/source-chat.service';

@Component({
  standalone: true,
  selector: 'app-source-chat-panel',
  imports: [FormsModule],
  templateUrl: './source-chat-panel.component.html',
})
export class SourceChatPanelComponent {
  @Input({ required: true }) sourceId = '';
  prompt = '';
  includeInsights = true;
  includeNotes = false;
  loading = false;
  error = '';
  response: any = null;

  constructor(private readonly chat: SourceChatService) {}

  send(): void {
    if (!this.prompt.trim()) { this.error = 'prompt_required'; return; }
    this.loading = true;
    this.error = '';
    this.chat.ask(this.sourceId, this.prompt, this.includeInsights, this.includeNotes).subscribe({
      next: response => { this.response = response; this.loading = false; },
      error: error => {
        this.error = String(error?.error?.error || error?.message || 'source_chat_failed');
        this.loading = false;
      },
    });
  }
}
