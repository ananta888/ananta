import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';

@Component({
  standalone: true,
  selector: 'app-templates',
  imports: [CommonModule, FormsModule],
  template: `
    <h2>Templates (Hub)</h2>
    <p class="muted">CRUD auf dem Hub-Agenten. Der Hub wird über die Agent-Liste (Rolle=hub) bestimmt.</p>

    <div class="card grid">
      <label>Name <input [(ngModel)]="form.name" placeholder="Name"></label>
      <label>Beschreibung <input [(ngModel)]="form.description" placeholder="Beschreibung"></label>
      <label>Prompt Template
        <textarea [(ngModel)]="form.prompt_template" rows="4" placeholder="{{ promptTemplateHint }}"></textarea>
      </label>
      <div class="row">
        <button (click)="create()">Anlegen</button>
        <span class="danger" *ngIf="err">{{err}}</span>
      </div>
    </div>

    <div class="grid cols-2" *ngIf="items?.length">
      <div class="card" *ngFor="let t of items">
        <div class="row" style="justify-content: space-between;">
          <strong>{{t.name}}</strong>
          <button (click)="del(t.id)" class="danger">Löschen</button>
        </div>
        <div class="muted">{{t.description}}</div>
        <details style="margin-top:8px">
          <summary>Prompt</summary>
          <pre style="white-space: pre-wrap">{{t.prompt_template}}</pre>
        </details>
      </div>
    </div>
  `
})
export class TemplatesComponent {
  items: any[] = [];
  err = '';
  form: any = { name: '', description: '', prompt_template: '' };
  promptTemplateHint = 'Use variables like {{title}} to generate prompts.';
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor(private dir: AgentDirectoryService, private hubApi: HubApiService){
    this.refresh();
  }
  refresh(){ if(!this.hub) return; this.hubApi.listTemplates(this.hub.url).subscribe({ next: r => this.items = r }); }
  create(){
    if(!this.hub) { this.err = 'Kein Hub konfiguriert'; return; }
    this.hubApi.createTemplate(this.hub.url, this.form, this.hub.token).subscribe({
      next: () => { this.form = { name: '', description: '', prompt_template: '' }; this.err=''; this.refresh(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }
  del(id: string){
    if(!this.hub) return;
    this.hubApi.deleteTemplate(this.hub.url, id, this.hub.token).subscribe({ next: () => this.refresh() });
  }
}
