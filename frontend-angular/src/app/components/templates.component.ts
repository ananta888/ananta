import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-templates',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Templates (Hub)</h2>
      <button (click)="refresh()" class="button-outline">🔄 Aktualisieren</button>
    </div>
    <p class="muted">Verwalten und erstellen Sie Prompt-Templates mithilfe von KI.</p>

    <div class="card grid">
      <label>Name <input [(ngModel)]="form.name" placeholder="Name"></label>
      <label>Beschreibung <input [(ngModel)]="form.description" placeholder="Beschreibung"></label>
      <label>Prompt Template
        <textarea [(ngModel)]="form.prompt_template" rows="6" placeholder="{{ promptTemplateHint }}"></textarea>
      </label>
      <div class="row">
        <button (click)="create()">Anlegen / Speichern</button>
        <button (click)="toggleChat()" class="button-outline">KI-Chat Support</button>
        <button (click)="form = { name: '', description: '', prompt_template: '' }" class="button-outline">Neu</button>
        <span class="danger" *ngIf="err">{{err}}</span>
      </div>
    </div>

    <!-- KI Chat Bereich -->
    <div class="card" *ngIf="showChat" style="margin-top: 20px; border-left: 4px solid #007bff; background: #fdfdfd;">
      <div class="row" style="justify-content: space-between;">
        <h3>KI Template-Designer</h3>
        <button (click)="showChat = false" class="button-outline" style="padding: 2px 8px; font-size: 12px;">Schließen</button>
      </div>
      <p class="muted" style="font-size: 12px;">Chatten Sie mit der KI, um das Template oben zu erstellen oder zu verfeinern.</p>
      
      <div #chatBox style="max-height: 250px; overflow-y: auto; margin-bottom: 10px; background: #fff; padding: 10px; border: 1px solid #eee; border-radius: 4px;">
        <div *ngFor="let msg of chatHistory" [style.text-align]="msg.role === 'user' ? 'right' : 'left'" style="margin-bottom: 10px;">
          <div [style.background]="msg.role === 'user' ? '#007bff' : '#f1f1f1'" 
               [style.color]="msg.role === 'user' ? 'white' : 'black'"
               style="display: inline-block; padding: 8px 12px; border-radius: 15px; max-width: 85%; font-size: 14px; line-height: 1.4;">
            {{msg.content}}
          </div>
        </div>
        <div *ngIf="busy" class="muted" style="font-size: 12px;">KI analysiert...</div>
      </div>
      
      <div class="row">
        <input [(ngModel)]="chatInput" (keyup.enter)="sendChat()" placeholder="z.B. Mach es kürzer oder füge Variablen hinzu..." style="flex-grow: 1;">
        <button (click)="sendChat()" [disabled]="busy || !chatInput">Senden</button>
      </div>
    </div>

    <div class="grid cols-2" *ngIf="items?.length" style="margin-top: 20px;">
      <div class="card" *ngFor="let t of items">
        <div class="row" style="justify-content: space-between;">
          <strong>{{t.name}}</strong>
          <div class="row">
             <button (click)="edit(t)" class="button-outline" style="padding: 4px 8px; font-size: 12px;">Edit</button>
             <button (click)="del(t.id)" class="danger" style="padding: 4px 8px; font-size: 12px;">Löschen</button>
          </div>
        </div>
        <div class="muted">{{t.description}}</div>
        <details style="margin-top:8px">
          <summary>Prompt ansehen</summary>
          <pre style="white-space: pre-wrap; font-size: 12px; background: #f4f4f4; padding: 8px;">{{t.prompt_template}}</pre>
        </details>
      </div>
    </div>
  `
})
export class TemplatesComponent {
  items: any[] = [];
  err = '';
  busy = false;
  form: any = { name: '', description: '', prompt_template: '' };
  promptTemplateHint = 'Nutzen Sie Variablen wie {{title}} in Ihren Prompts.';
  hub = this.dir.list().find(a => a.role === 'hub');

  showChat = false;
  chatInput = '';
  chatHistory: { role: 'user' | 'assistant', content: string }[] = [];

  constructor(
    private dir: AgentDirectoryService, 
    private hubApi: HubApiService, 
    private agentApi: AgentApiService,
    private ns: NotificationService
  ){
    this.refresh();
  }

  refresh(){ 
    if(!this.hub) return; 
    this.hubApi.listTemplates(this.hub.url).subscribe({ 
        next: r => this.items = r,
        error: () => this.ns.error('Templates konnten nicht geladen werden')
    }); 
  }
  
  toggleChat() {
    this.showChat = !this.showChat;
    if (this.showChat && this.chatHistory.length === 0) {
      this.chatHistory.push({ role: 'assistant', content: 'Hallo! Ich helfe dir, ein effektives Agenten-Prompt-Template zu erstellen. Was soll der Agent tun?' });
    }
  }

  sendChat() {
    if (!this.hub || !this.chatInput.trim()) return;
    
    const userMsg = this.chatInput;
    this.chatHistory.push({ role: 'user', content: userMsg });
    this.chatInput = '';
    this.busy = true;

    // Kontext für die KI zusammenbauen
    const context = `
Aktuelles Template-Name: ${this.form.name || 'Unbenannt'}
Aktuelle Beschreibung: ${this.form.description || 'Keine'}
Aktueller Prompt-Text: 
---
${this.form.prompt_template || '(Leer)'}
---
Anweisung des Nutzers: ${userMsg}

Antworte im folgenden Format:
LOGIK: (Deine kurze Erklärung was du geändert hast)
NAME: (Vorschlag für Name, falls geändert)
BESCHREIBUNG: (Vorschlag für Beschreibung, falls geändert)
TEMPLATE:
(Hier das vollständige neue Template-Text)
`;

    this.agentApi.llmGenerate(this.hub.url, context, null, this.hub.token).subscribe({
      next: r => {
        const resp = r.response;
        const logic = this.extractPart(resp, 'LOGIK') || 'Template wurde aktualisiert.';
        this.chatHistory.push({ role: 'assistant', content: logic });
        
        // Formular aktualisieren
        const newName = this.extractPart(resp, 'NAME');
        if (newName) this.form.name = newName;
        
        const newDesc = this.extractPart(resp, 'BESCHREIBUNG');
        if (newDesc) this.form.description = newDesc;
        
        const templateMarker = 'TEMPLATE:';
        const templateIdx = resp.indexOf(templateMarker);
        if (templateIdx !== -1) {
            this.form.prompt_template = resp.substring(templateIdx + templateMarker.length).trim();
        }
      },
      error: () => { this.ns.error('KI-Chat fehlgeschlagen'); },
      complete: () => { this.busy = false; }
    });
  }

  private extractPart(text: string, marker: string): string {
    const lines = text.split('\n');
    for (const line of lines) {
      if (line.toUpperCase().startsWith(marker + ':')) {
        return line.substring(marker.length + 1).trim();
      }
    }
    return '';
  }

  create(){
    if(!this.hub) { this.err = 'Kein Hub konfiguriert'; return; }
    if(!this.form.name || !this.form.prompt_template) { this.ns.error('Name und Template sind erforderlich'); return; }
    
    const obs = this.form.id 
        ? this.hubApi.updateTemplate(this.hub.url, this.form.id, this.form, this.hub.token)
        : this.hubApi.createTemplate(this.hub.url, this.form, this.hub.token);

    obs.subscribe({
      next: () => { 
        this.form = { name: '', description: '', prompt_template: '' }; 
        this.err=''; 
        this.ns.success('Template gespeichert');
        this.refresh(); 
      },
      error: () => { this.ns.error('Fehler beim Speichern'); }
    });
  }

  edit(t: any) {
    this.form = { ...t };
    this.ns.info('Template in Editor geladen');
  }

  del(id: string){
    if(!this.hub || !confirm('Template wirklich löschen?')) return;
    this.hubApi.deleteTemplate(this.hub.url, id, this.hub.token).subscribe({ 
        next: () => { this.ns.success('Gelöscht'); this.refresh(); } 
    });
  }
}
