import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';

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
    <div *ngIf="!isAdmin" class="muted" style="margin-bottom: 10px;">
      Template-Verwaltung ist nur für Admins verfügbar.
    </div>

    <div class="card grid">
      <div class="row" style="gap: 10px; align-items: flex-end; margin-bottom: 15px; background: #f0f7ff; padding: 10px; border-radius: 4px;">
        <label style="flex: 1; margin-bottom: 0;">KI-Unterstützung ({{templateAgent?.name || 'Hub'}})
          <input [(ngModel)]="aiPrompt" placeholder="Beschreibe das Template (z.B. 'Ein Template für Code-Reviews')" [disabled]="busy || !isAdmin">
        </label>
        <button (click)="generateAI()" [disabled]="busy || !aiPrompt || !isAdmin" class="button-outline" style="margin-bottom: 0;">🪄 Entwurf</button>
      </div>

      <label>Name <input [(ngModel)]="form.name" placeholder="Name" [disabled]="!isAdmin"></label>
      <label>Beschreibung <input [(ngModel)]="form.description" placeholder="Beschreibung" [disabled]="!isAdmin"></label>
      <label>Prompt Template
        <textarea [(ngModel)]="form.prompt_template" rows="6" placeholder="{{ promptTemplateHint }}" [disabled]="!isAdmin"></textarea>
      </label>
      <div style="font-size: 11px; margin-bottom: 10px;" class="muted">
        Erlaubte Variablen: <span *ngFor="let v of allowedVars" style="margin-right: 8px; border-bottom: 1px dotted #ccc;" [title]="'Variable: {{'+v+'}}'">{{ '{' + '{' + v + '}' + '}' }}</span>
      </div>
      <div *ngIf="getUnknownVars().length > 0" class="danger" style="font-size: 12px; margin-bottom: 10px;">
        ⚠️ Unbekannte Variablen: {{ getUnknownVars().join(', ') }}
      </div>
      <div class="row">
        <button (click)="create()" [disabled]="!isAdmin">Anlegen / Speichern</button>
        <button (click)="form = { name: '', description: '', prompt_template: '' }" class="button-outline" [disabled]="!isAdmin">Neu</button>
        <span class="danger" *ngIf="err">{{err}}</span>
      </div>
    </div>

    <div class="grid cols-2" *ngIf="items?.length" style="margin-top: 20px;">
      <div class="card" *ngFor="let t of items">
        <div class="row" style="justify-content: space-between;">
          <strong>{{t.name}}</strong>
          <div class="row">
             <button (click)="edit(t)" class="button-outline" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Edit</button>
             <button (click)="del(t.id)" class="danger" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Löschen</button>
          </div>
        </div>
        <div class="muted">{{t.description}}</div>
        <div class="muted" style="font-size: 11px; margin-top: 4px;">
          Nutzung: Rollen {{getRoleUsageCount(t.id)}}, Typ-Zuordnung {{getTypeUsageCount(t.id)}}, Team-Mitglieder {{getMemberUsageCount(t.id)}}
        </div>
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
  roles: any[] = [];
  teams: any[] = [];
  teamTypes: any[] = [];
  err = '';
  busy = false;
  aiPrompt = '';
  form: any = { name: '', description: '', prompt_template: '' };
  promptTemplateHint = 'Verwenden Sie {{variable}} für Platzhalter.';
  allowedVars = ["agent_name", "task_title", "task_description", "team_name", "role_name", "team_goal", "anforderungen", "funktion", "feature_name", "title"];
  hub = this.dir.list().find(a => a.role === 'hub');
  templateAgent: any;
  isAdmin = false;

  constructor(
    private dir: AgentDirectoryService, 
    private hubApi: HubApiService, 
    private agentApi: AgentApiService,
    private ns: NotificationService,
    private userAuth: UserAuthService
  ){
    this.userAuth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.refresh();
  }

  generateAI() {
    if (!this.isAdmin || !this.aiPrompt.trim()) return;
    const target = this.templateAgent || this.hub;
    if (!target) return;

    this.busy = true;
    const p = `Erstelle ein Prompt-Template für folgendes Szenario: ${this.aiPrompt}. 
    Antworte im JSON Format mit den Feldern 'name', 'description' und 'prompt_template'.`;

    this.agentApi.llmGenerate(target.url, p, null).subscribe({
      next: r => {
        try {
          let data = r.response;
          if (typeof data === 'string') {
            const start = data.indexOf('{');
            const end = data.lastIndexOf('}');
            if (start !== -1 && end !== -1) {
              data = JSON.parse(data.substring(start, end + 1));
            }
          }
          
          this.form.name = data.name || this.form.name;
          this.form.description = data.description || this.form.description;
          this.form.prompt_template = data.prompt_template || data.template || (typeof data === 'string' ? data : this.form.prompt_template);
          this.ns.success('KI-Entwurf geladen');
        } catch(e) {
          this.form.prompt_template = r.response;
          this.ns.info('KI-Antwort geladen');
        }
        this.busy = false;
        this.aiPrompt = '';
      },
      error: () => {
        this.ns.error('KI-Generierung fehlgeschlagen');
        this.busy = false;
      }
    });
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
        next: r => this.items = Array.isArray(r) ? r : [],
        error: () => this.ns.error('Templates konnten nicht geladen werden')
    }); 

    this.hubApi.listTeamRoles(this.hub.url).subscribe({
      next: r => this.roles = Array.isArray(r) ? r : [],
      error: () => {}
    });

    this.hubApi.listTeams(this.hub.url).subscribe({
      next: r => this.teams = Array.isArray(r) ? r : [],
      error: () => {}
    });

    this.hubApi.listTeamTypes(this.hub.url).subscribe({
      next: r => this.teamTypes = Array.isArray(r) ? r : [],
      error: () => {}
    });
  }

  getRoleUsageCount(templateId: string): number {
    return this.roles.filter(r => r.default_template_id === templateId).length;
  }

  getTypeUsageCount(templateId: string): number {
    let count = 0;
    for (const t of this.teamTypes) {
      const mappings = t.role_templates || {};
      for (const roleId of Object.keys(mappings)) {
        if (mappings[roleId] === templateId) count += 1;
      }
    }
    return count;
  }

  getMemberUsageCount(templateId: string): number {
    let count = 0;
    for (const team of this.teams) {
      for (const member of team.members || []) {
        if (member.custom_template_id === templateId) count += 1;
      }
    }
    return count;
  }

  getUnknownVars(): string[] {
    if (!this.form.prompt_template) return [];
    const matches = this.form.prompt_template.match(/\{\{([a-zA-Z0-9_]+)\}\}/g) || [];
    const vars = matches.map((m: string) => m.replace(/\{\{|\}\}/g, ''));
    return vars.filter((v: string) => !this.allowedVars.includes(v));
  }
  
  create(){
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
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
      error: (e) => { 
        if (e.error && e.error.details) {
          this.err = e.error.details;
          this.ns.error(e.error.details);
        } else {
          this.ns.error('Fehler beim Speichern'); 
        }
      }
    });
  }

  edit(t: any) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    this.form = { ...t };
    this.ns.info('Template in Editor geladen');
  }

  del(id: string){
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if(!this.hub || !confirm('Template wirklich löschen?')) return;
    this.hubApi.deleteTemplate(this.hub.url, id).subscribe({ 
        next: () => { this.ns.success('Gelöscht'); this.refresh(); } 
    });
  }
}
