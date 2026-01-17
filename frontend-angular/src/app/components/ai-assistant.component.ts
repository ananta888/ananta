import { Component, OnInit, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ai-assistant-container" [class.minimized]="minimized">
      <div class="header" (click)="toggleMinimize()">
        <span>✨ KI Assistent</span>
        <div class="controls">
          <button (click)="toggleMinimize(); $event.stopPropagation()" class="control-btn">
            {{ minimized ? '▲' : '▼' }}
          </button>
        </div>
      </div>
      
      <div class="content" *ngIf="!minimized">
        <div #chatBox class="chat-history">
          <div *ngFor="let msg of chatHistory" [style.text-align]="msg.role === 'user' ? 'right' : 'left'" style="margin-bottom: 10px;">
            <div [style.background]="msg.role === 'user' ? '#007bff' : '#f1f1f1'" 
                 [style.color]="msg.role === 'user' ? 'white' : 'black'"
                 style="display: inline-block; padding: 8px 12px; border-radius: 15px; max-width: 85%; font-size: 14px; line-height: 1.4;">
              {{msg.content}}
            </div>
          </div>
          <div *ngIf="busy" class="muted" style="font-size: 12px;">KI denkt nach...</div>
        </div>
        
        <div class="input-area">
          <input [(ngModel)]="chatInput" (keyup.enter)="sendChat()" placeholder="Frage mich etwas..." [disabled]="busy">
          <button (click)="sendChat()" [disabled]="busy || !chatInput.trim()">Senden</button>
        </div>
      </div>
    </div>

    <style>
      .ai-assistant-container {
        position: fixed;
        bottom: 0;
        right: 20px;
        width: 350px;
        background: white;
        border: 1px solid #ddd;
        border-radius: 8px 8px 0 0;
        box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
        z-index: 1000;
        display: flex;
        flex-direction: column;
        transition: height 0.3s ease;
      }
      .ai-assistant-container.minimized {
        height: 40px;
      }
      .header {
        background: #007bff;
        color: white;
        padding: 8px 15px;
        border-radius: 8px 8px 0 0;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: bold;
      }
      .content {
        height: 400px;
        display: flex;
        flex-direction: column;
        padding: 10px;
      }
      .chat-history {
        flex-grow: 1;
        overflow-y: auto;
        margin-bottom: 10px;
        padding-right: 5px;
      }
      .input-area {
        display: flex;
        gap: 5px;
      }
      .input-area input {
        flex-grow: 1;
        padding: 8px;
        border: 1px solid #ddd;
        border-radius: 4px;
      }
      .control-btn {
        background: none;
        border: none;
        color: white;
        cursor: pointer;
        font-size: 12px;
      }
      .muted { color: #666; }
    </style>
  `
})
export class AiAssistantComponent implements OnInit, AfterViewChecked {
  @ViewChild('chatBox') private chatBox?: ElementRef;

  minimized = true;
  busy = false;
  chatInput = '';
  chatHistory: { role: 'user' | 'assistant', content: string }[] = [];
  
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor(
    private dir: AgentDirectoryService,
    private agentApi: AgentApiService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.chatHistory.push({ role: 'assistant', content: 'Hallo! Ich bin dein globaler KI-Assistent. Wie kann ich dir heute helfen?' });
  }

  ngAfterViewChecked() {
    this.scrollToBottom();
  }

  toggleMinimize() {
    this.minimized = !this.minimized;
  }

  sendChat() {
    if (!this.hub || !this.chatInput.trim()) return;
    
    const userMsg = this.chatInput;
    this.chatHistory.push({ role: 'user', content: userMsg });
    this.chatInput = '';
    this.busy = true;

    this.agentApi.llmGenerate(this.hub.url, userMsg, null).subscribe({
      next: r => {
        this.chatHistory.push({ role: 'assistant', content: r.response });
      },
      error: () => { this.ns.error('KI-Chat fehlgeschlagen'); },
      complete: () => { this.busy = false; }
    });
  }

  private scrollToBottom(): void {
    if (this.chatBox) {
      this.chatBox.nativeElement.scrollTop = this.chatBox.nativeElement.scrollHeight;
    }
  }
}
