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
        <button (click)="form = { name: '', description: '', prompt_template: '' }" class="button-outline">Neu</button>
        <span class="danger" *ngIf="err">{{err}}</span>
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
  templateAgent: any;

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
    
    // Konfiguration laden um Template-Agent zu finden
    this.agentApi.getConfig(this.hub.url).subscribe({
      next: cfg => {
        if (cfg.template_agent_name) {
          this.templateAgent = this.dir.list().find(a => a.name === cfg.template_agent_name);
        } else {
          this.templateAgent = this.hub;
        }
      }
    });

    this.hubApi.listTemplates(this.hub.url).subscribe({
        next: r => this.items = r,
        error: () => this.ns.error('Templates konnten nicht geladen werden')
    }); 
  }
  
  create(){
    if(!this.hub) { this.err = 'Kein Hub konfiguriert'; return; }
    if(!this.form.name || !this.form.prompt_template) { this.ns.error('Name und Template sind erforderlich'); return; }
    
    const obs = this.form.id 
        ? this.hubApi.updateTemplate(this.hub.url, this.form.id, this.form)
        : this.hubApi.createTemplate(this.hub.url, this.form);

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
    this.hubApi.deleteTemplate(this.hub.url, id).subscribe({ 
        next: () => { this.ns.success('Gelöscht'); this.refresh(); } 
    });
  }
}
