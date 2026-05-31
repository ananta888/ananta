import { AfterViewChecked, Component, ElementRef, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { ChatMessage, ContextSource } from './ai-assistant.types';

@Component({
  standalone: true,
  selector: 'app-ai-assistant-message-list',
  imports: [CommonModule, FormsModule],
  template: `
    <div #chatBox class="chat-history">
      @for (msg of chatHistory; track msg) {
        <div [style.text-align]="msg.role === 'user' ? 'right' : 'left'" class="msg-row">
          <div class="msg-bubble" [class.user-msg]="msg.role === 'user'" [class.assistant-msg]="msg.role === 'assistant'">
            <div [innerHTML]="renderMarkdown(msg.content)"></div>
            @if (msg.cliBackendUsed) {
              <div class="muted msg-meta">CLI backend: {{ msg.cliBackendUsed }}</div>
            }
            @if (msg.routing) {
              <div class="muted msg-meta">Routing: requested={{ msg.routing.requestedBackend || 'n/a' }}, effective={{ msg.routing.effectiveBackend || msg.cliBackendUsed || 'n/a' }}, reason={{ msg.routing.reason || 'n/a' }}</div>
            }
            @if (msg.contextMeta) {
              <div class="context-panel">
                <div class="context-title">Context Debug</div>
                <div class="context-meta">
                  policy={{ msg.contextMeta.policy_version || 'v1' }} |
                  chunks={{ msg.contextMeta.chunk_count || 0 }} |
                  tokens={{ msg.contextMeta.token_estimate || 0 }}
                </div>
                <div class="context-meta">strategy={{ msg.contextMeta.strategy | json }}</div>
                @if (msg.contextMeta.explainability) {
                  <div class="context-meta">
                    collections={{ (msg.contextMeta.explainability.collection_names || []).join(', ') || 'n/a' }} |
                    chunk_types={{ (msg.contextMeta.explainability.chunk_types || []).join(', ') || 'n/a' }}
                  </div>
                  <div class="context-meta">
                    artifacts={{ (msg.contextMeta.explainability.artifact_ids || []).join(', ') || 'n/a' }} |
                    indices={{ (msg.contextMeta.explainability.knowledge_index_ids || []).join(', ') || 'n/a' }}
                  </div>
                }
                @if (msg.contextSources?.length) {
                  <div class="context-sources">
                    @for (c of msg.contextSources; track c) {
                      <div class="context-source-row">
                        <div>[{{ c.engine }}] {{ c.source }}</div>
                        <div class="context-meta">
                          type={{ c.recordKind || 'n/a' }} |
                          artifact={{ c.artifactId || 'n/a' }} |
                          collections={{ (c.collectionNames || []).join(', ') || 'n/a' }}
                        </div>
                        <div class="context-actions">
                          <button class="mini-btn" (click)="previewSource.emit(c)">Preview</button>
                          <button class="mini-btn" (click)="copySourcePath.emit(c.source)">Copy Path</button>
                        </div>
                        @if (c.previewLoading) {
                          <pre class="source-preview">Loading...</pre>
                        }
                        @if (c.previewError) {
                          <pre class="source-preview">{{ c.previewError }}</pre>
                        }
                        @if (c.preview) {
                          <pre class="source-preview">{{ c.preview }}</pre>
                        }
                      </div>
                    }
                  </div>
                }
              </div>
            }
            @if (msg.sgptCommand) {
              <div class="sgpt-section">
                <div class="sgpt-label">
                  <strong>Shell command:</strong>
                  <pre class="sgpt-code">{{msg.sgptCommand}}</pre>
                </div>
                <div class="sgpt-actions">
                  <button (click)="executeSgpt.emit(msg)" class="confirm-btn">Run</button>
                  <button (click)="clearSgptCommand(msg)" class="cancel-btn">Ignore</button>
                </div>
              </div>
            }
            @if (msg.requiresConfirmation) {
              <div class="sgpt-section">
                <div class="plan-header">Planned actions</div>
                @if (msg.planRisk) {
                  <div class="muted plan-risk">Risk: <strong>{{ msg.planRisk.level }}</strong> - {{ msg.planRisk.reason }}</div>
                }
                @for (tc of msg.toolCalls; track tc) {
                  <div class="tool-card">
                    <div><strong>{{ formatToolName(tc?.name) }}</strong></div>
                    <div class="muted tool-meta">Scope: {{ summarizeToolScope(tc) }}</div>
                    <div class="muted tool-meta">Expected: {{ summarizeToolImpact(tc) }}</div>
                    <div class="muted tool-meta">Changes: {{ summarizeToolChanges(tc) }}</div>
                    <details class="raw-args">
                      <summary style="cursor: pointer;">Raw args</summary>
                      <pre class="raw-args-code">{{ tc?.args | json }}</pre>
                    </details>
                  </div>
                }
                <div class="muted confirm-hint">Type <strong>RUN</strong> to confirm execution.</div>
                <input [(ngModel)]="msg.confirmationText" placeholder="Type RUN" class="confirm-input" />
                <div class="sgpt-actions">
                  <button (click)="confirmAction.emit(msg)" class="confirm-btn" [disabled]="(msg.confirmationText || '').trim().toUpperCase() !== 'RUN'">Run Plan</button>
                  <button (click)="cancelAction.emit(msg)" class="cancel-btn">Cancel Plan</button>
                </div>
              </div>
            }
          </div>
        </div>
      }
      @if (busy) {
        <div class="muted working-text">Working...</div>
      }
    </div>
  `,
  styles: [`
    :host { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .chat-history {
      flex-grow: 1; overflow-y: auto; margin-bottom: 8px; padding-right: 4px;
      font-family: ui-monospace, Menlo, Consolas, monospace;
    }
    .chat-history::-webkit-scrollbar { width: 4px; }
    .chat-history::-webkit-scrollbar-track { background: #0b1220; }
    .chat-history::-webkit-scrollbar-thumb { background: #1a2d4a; border-radius: 2px; }
    .msg-row { margin-bottom: 6px; display: flex; }
    .msg-row[style*="right"] { justify-content: flex-end; }
    .msg-bubble {
      display: inline-block; padding: 6px 10px; max-width: 88%;
      font-size: 13px; line-height: 1.5; text-align: left; word-break: break-word;
      border-radius: 3px; white-space: pre-wrap;
    }
    .user-msg {
      background: #162238; color: #a8c7ff;
      border: 1px solid #2a4070; border-radius: 3px 3px 2px 3px;
    }
    .assistant-msg {
      background: #0f1c30; color: #c8d8f8;
      border: 1px solid #1a3058; border-radius: 3px 3px 3px 2px;
    }
    .assistant-msg pre {
      background: #0d1828; color: #7fffd4; padding: 7px; border-radius: 2px;
      overflow-x: auto; font-family: inherit; margin: 5px 0;
      border: 1px solid #1a2d4a; font-size: 12px;
    }
    .assistant-msg code {
      background: #0d1828; color: #7fffd4; padding: 1px 4px;
      border-radius: 2px; font-family: inherit; font-size: 12px;
    }
    .working-text { color: #7fffd4; font-size: 12px; padding: 4px 2px; animation: blink 1s step-start infinite; }
    @keyframes blink { 50% { opacity: 0.3; } }
    .msg-meta { font-size: 10px; color: #3a5a8f; margin-top: 3px; }
    .context-panel { margin-top: 8px; border-top: 1px solid #1a2d4a; padding-top: 6px; font-size: 11px; color: #6b8ab8; }
    .context-title { font-weight: 600; margin-bottom: 3px; color: #a8c7ff; }
    .context-meta { color: #4a6a9a; }
    .context-sources { margin-top: 4px; max-height: 80px; overflow-y: auto; }
    .context-source-row { margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #131e36; }
    .context-actions { margin-top: 3px; display: flex; gap: 5px; }
    .mini-btn {
      font-size: 10px; padding: 2px 6px; border: 1px solid #1a2d4a;
      background: transparent; color: #6b8ab8; cursor: pointer; border-radius: 2px; font-family: inherit;
    }
    .mini-btn:hover { border-color: #2a4070; color: #c8d8f8; }
    .source-preview {
      margin-top: 3px; max-height: 100px; overflow-y: auto;
      background: #0d1828; padding: 5px; border-radius: 2px;
      font-size: 11px; color: #7fffd4; border: 1px solid #1a2d4a;
    }
    .sgpt-section { margin-top: 8px; border-top: 1px solid #1a2d4a; padding-top: 6px; }
    .sgpt-label { font-size: 11px; color: #6b8ab8; margin-bottom: 4px; }
    .sgpt-code {
      background: #0d1828; color: #fbbf24; padding: 5px; border-radius: 2px;
      font-family: inherit; font-size: 12px; margin: 3px 0; border: 1px solid #1a2d4a;
    }
    .sgpt-actions { display: flex; gap: 6px; margin-top: 5px; }
    .plan-header { font-weight: 600; color: #a8c7ff; margin-bottom: 4px; font-size: 12px; }
    .plan-risk { font-size: 11px; margin-bottom: 4px; }
    .tool-card {
      background: #0d1828; border: 1px solid #1a2d4a; border-radius: 2px;
      padding: 5px 8px; margin-bottom: 4px; font-size: 11px;
    }
    .tool-meta { color: #4a6a9a; }
    .raw-args { font-size: 10px; }
    .raw-args-code { background: #0b1220; padding: 4px; font-size: 10px; color: #6b8ab8; }
    .confirm-hint { font-size: 11px; color: #6b8ab8; margin: 4px 0; }
    .confirm-input {
      width: 100%; box-sizing: border-box; background: #0f1c30; border: 1px solid #1a2d4a;
      color: #c8d8f8; padding: 4px 7px; font-family: inherit; font-size: 12px; border-radius: 2px;
    }
    .confirm-btn {
      background: #0f2a1a; color: #7fffd4; border: 1px solid #1a4a2a;
      padding: 3px 8px; border-radius: 2px; cursor: pointer; font-size: 12px; font-family: inherit;
    }
    .cancel-btn {
      background: #2a0f0f; color: #fb7185; border: 1px solid #4a1a1a;
      padding: 3px 8px; border-radius: 2px; cursor: pointer; font-size: 12px; font-family: inherit;
    }
  `]
})
export class AiAssistantMessageListComponent implements AfterViewChecked {
  @Input() chatHistory: ChatMessage[] = [];
  @Input() busy = false;

  @Output() previewSource = new EventEmitter<ContextSource>();
  @Output() copySourcePath = new EventEmitter<string>();
  @Output() executeSgpt = new EventEmitter<ChatMessage>();
  @Output() confirmAction = new EventEmitter<ChatMessage>();
  @Output() cancelAction = new EventEmitter<ChatMessage>();

  @ViewChild('chatBox') private chatBox?: ElementRef;

  constructor(private readonly domain: AiAssistantDomainService) {}

  ngAfterViewChecked(): void {
    if (this.chatBox) {
      this.chatBox.nativeElement.scrollTop = this.chatBox.nativeElement.scrollHeight;
    }
  }

  renderMarkdown(text: string): string {
    if (!text) return '';
    const rendered = marked.parse(text, { breaks: true });
    const html = typeof rendered === 'string' ? rendered : '';
    return DOMPurify.sanitize(html);
  }

  formatToolName(name?: string): string {
    return this.domain.formatToolName(name);
  }

  summarizeToolScope(tc: any): string {
    return this.domain.summarizeToolScope(tc);
  }

  summarizeToolImpact(tc: any): string {
    return this.domain.summarizeToolImpact(tc);
  }

  summarizeToolChanges(tc: any): string {
    return this.domain.summarizeToolChanges(tc);
  }

  clearSgptCommand(msg: ChatMessage): void {
    msg.sgptCommand = undefined;
  }
}
