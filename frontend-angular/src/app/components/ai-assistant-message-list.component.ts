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
    .chat-history { flex-grow: 1; overflow-y: auto; margin-bottom: 10px; padding-right: 5px; }
    .msg-bubble { display: inline-block; padding: 8px 12px; border-radius: 15px; max-width: 90%; font-size: 14px; line-height: 1.4; text-align: left; white-space: pre-wrap; }
    .user-msg { background: var(--accent); color: white; border-bottom-right-radius: 2px; }
    .assistant-msg { background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-bottom-left-radius: 2px; }
    .assistant-msg pre { background: #1e1e1e; color: #d4d4d4; padding: 8px; border-radius: 4px; overflow-x: auto; font-family: monospace; margin: 5px 0; }
    .assistant-msg code { background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 3px; font-family: monospace; }
    .context-panel { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; font-size: 12px; }
    .context-title { font-weight: 600; margin-bottom: 4px; }
    .context-meta { opacity: 0.9; }
    .context-sources { margin-top: 5px; max-height: 90px; overflow-y: auto; }
    .context-source-row { margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px dotted var(--border); }
    .context-actions { margin-top: 4px; display: flex; gap: 6px; }
    .mini-btn { font-size: 11px; padding: 2px 6px; border: 1px solid var(--border); background: transparent; color: var(--fg); cursor: pointer; }
    .source-preview { margin-top: 4px; max-height: 120px; overflow-y: auto; background: rgba(0,0,0,0.15); padding: 6px; border-radius: 4px; }
    .confirm-btn { background: #28a745; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
    .cancel-btn { background: #dc3545; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
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
