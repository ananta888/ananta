import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { ChatSettingControlsComponent } from './chat-setting-controls.component';
import { ChatProcessBindingEditorComponent } from './chat-process-binding-editor.component';
import {
  ChatProfile,
  ChatSessionsService,
  ChatSettingDefinition,
  ChatSettingValue,
  ChatSettingsMap,
} from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-profile-editor',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatSettingControlsComponent, ChatProcessBindingEditorComponent],
  template: `
    <section class="profile-editor">
      <header><strong>Wiederverwendbare Chat-Profile</strong><button (click)="newProfile()">＋ Profil</button></header>
      <div class="layout">
        <nav>
          @for (profile of profiles; track profile.id) {
            <button [class.active]="profile.id === selectedId" (click)="select(profile)">
              {{ profile.icon || '🎯' }} {{ profile.name }} <small>{{ profile.builtin ? 'integriert' : '' }}</small>
            </button>
          }
        </nav>
        <div class="form">
          @if(pendingProfile){<div class="dirty-warning" role="alert">Ungespeicherte Änderungen: <button (click)="saveThenSwitch()">Speichern</button><button (click)="discardSwitch()">Verwerfen</button><button (click)="pendingProfile=null">Abbrechen</button></div>}
          <div class="editor-tabs"><button (click)="editorTab='base'" [class.active]="editorTab==='base'">Profil</button><button (click)="editorTab='process'" [class.active]="editorTab==='process'">Prozess</button><button (click)="editorTab='settings'" [class.active]="editorTab==='settings'">Einstellungen</button></div>
          @if(editorTab==='base'){
          <div class="identity">
            <input [(ngModel)]="draft.icon" maxlength="4" [disabled]="readOnly" aria-label="Profil-Icon" />
            <input [(ngModel)]="draft.name" [disabled]="readOnly" placeholder="Profilname" aria-label="Profilname" />
          </div>
          <textarea [(ngModel)]="draft.system_prompt" [disabled]="readOnly" rows="3" placeholder="System-Prompt"></textarea>
          <textarea [(ngModel)]="draft.description" [disabled]="readOnly" rows="2" placeholder="Beschreibung"></textarea>
          }
          @if(editorTab==='settings'){
          <div class="tools"><input [(ngModel)]="search" placeholder="Einstellungen suchen" aria-label="Einstellungen suchen" />
            <label><input type="checkbox" [(ngModel)]="showAdvanced" /> Erweitert</label>
            <button (click)="discoverModels()" [disabled]="probing">Modelle laden</button>
            <button (click)="testConnection()" [disabled]="probing">Verbindung testen</button>
            <button (click)="loadPreview()">Effektive Vorschau</button>
          </div>
          @if (probeStatus) { <div class="probe" role="status">{{ probeStatus }}</div> }
          @if(previewJson){<details><summary>Effektive Werte</summary><button (click)="copyPreview()">JSON kopieren</button><pre>{{previewJson}}</pre></details>}
          <datalist id="chat-profile-models">@for (model of discoveredModels; track model) { <option [value]="model"></option> }</datalist>
          <app-chat-setting-controls [settings]="settings" scope="profile" [delta]="draft.settings"
            [effective]="profileDefaults()" [readOnly]="readOnly" overrideLabel="Profilwert"
            (changed)="setValue($event.key,$event.value)" (reset)="resetValue($event)" (resetAll)="resetAllValues()" />
          }
          @if(editorTab==='process'){
            <app-chat-process-binding-editor [processRef]="draft.process_ref" [readOnly]="readOnly" (processRefChange)="draft.process_ref=$event" />
          }
          <footer>
            @if (readOnly) {
              <button (click)="duplicate()">Als eigenes Profil duplizieren</button>
            } @else {
              <button (click)="save()" [disabled]="!draft.name.trim() || saving">{{ saving ? 'Speichere…' : 'Speichern' }}</button>
              <button (click)="cancel()">Abbrechen</button>
              @if (selectedId) { <button class="danger" (click)="remove()">Löschen</button> }
            }
          </footer>
        </div>
      </div>
    </section>
  `,
  styles: [`
    .profile-editor{padding:10px;border-bottom:1px solid #1a2d4a;background:#091526;color:#dce8f8}
    header,footer,.identity{display:flex;gap:8px;align-items:center} header{justify-content:space-between;margin-bottom:8px}
    .layout{display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:10px} nav{display:flex;flex-direction:column;gap:3px}
    nav button{text-align:left}.active{outline:1px solid #4ea1ff}.form{display:grid;gap:8px}.identity input:nth-child(2){flex:1}
    textarea{width:100%;box-sizing:border-box}.setting,.process-ref{display:grid;grid-template-columns:1fr minmax(120px,220px) auto;gap:7px;align-items:center;padding:3px}
    .setting.inherited{opacity:.72}.setting small,nav small{opacity:.65;margin-left:5px}details{border-top:1px solid #1a2d4a;padding-top:5px}
    summary{cursor:pointer}.danger{color:#ff8b8b}@media(max-width:700px){.layout{grid-template-columns:1fr}.setting{grid-template-columns:1fr auto auto}}
  `],
})
export class ChatProfileEditorComponent implements OnInit, OnDestroy {
  private readonly service = inject(ChatSessionsService);
  private readonly subscriptions: Subscription[] = [];
  profiles: ChatProfile[] = [];
  settings: ChatSettingDefinition[] = [];
  selectedId = '';
  readOnly = false;
  search = '';
  showAdvanced = false;
  saving = false;
  probing = false;
  probeStatus = '';
  discoveredModels: string[] = [];
  previewJson='';
  editorTab:'base'|'process'|'settings'='base';
  private originalSettings: ChatSettingsMap = {};
  private resetKeys = new Set<string>();
  pendingProfile:ChatProfile|null=null;private loadedSnapshot='';private afterSaveProfile:ChatProfile|null=null;
  draft = this.emptyDraft();

  get groups(): string[] { return [...new Set(this.settings.map(item => item.advanced ? 'Erweitert' : item.group))]; }

  ngOnInit(): void {
    this.subscriptions.push(
      this.service.profiles$.subscribe(profiles => { this.profiles = profiles; }),
      this.service.settingSchema$.subscribe(schema => { this.settings = schema.settings.filter(item => item.scopes.includes('profile')); }),
    );
  }
  ngOnDestroy(): void { this.subscriptions.forEach(subscription => subscription.unsubscribe()); }
  settingsFor(group: string): ChatSettingDefinition[] { const query=this.search.trim().toLowerCase(); return this.settings.filter(item => (item.advanced ? 'Erweitert' : item.group) === group && (!query || `${item.key} ${item.label}`.toLowerCase().includes(query))); }
  select(profile: ChatProfile, force=false): void {
    if(!force&&this.selectedId&&profile.id!==this.selectedId&&this.isDirty()){this.pendingProfile=profile;return;}
    this.selectedId = profile.id; this.readOnly = profile.builtin;
    this.originalSettings = { ...profile.settings }; this.resetKeys.clear();
    this.draft = { name: profile.name, icon: profile.icon || '🎯', description: profile.description || '', system_prompt: profile.system_prompt || '', settings: { ...profile.settings }, process_ref: profile.process_ref ? { ...profile.process_ref } : null };
    this.loadedSnapshot=JSON.stringify(this.draft);this.pendingProfile=null;
  }
  newProfile(): void { this.selectedId = ''; this.readOnly = false; this.draft = this.emptyDraft(); }
  duplicate(): void { this.selectedId = ''; this.readOnly = false; this.draft = { ...this.draft, name: `${this.draft.name} (Kopie)`, settings: { ...this.draft.settings } }; }
  cancel(): void { const profile = this.profiles.find(item => item.id === this.selectedId); profile ? this.select(profile) : this.newProfile(); }
  hasOverride(key: string): boolean { return Object.prototype.hasOwnProperty.call(this.draft.settings, key); }
  effective(setting: ChatSettingDefinition): unknown { return this.hasOverride(setting.key) ? this.draft.settings[setting.key] : setting.scope_defaults['profile'] ?? setting.default; }
  isVisible(setting: ChatSettingDefinition): boolean { return Object.entries(setting.visible_when || {}).every(([key,values]) => values.includes(this.draft.settings[key] ?? this.settings.find(item=>item.key===key)?.scope_defaults['profile'])); }
  setValue(key: string, value: ChatSettingValue): void { this.draft.settings = { ...this.draft.settings, [key]: value }; }
  setTypedValue(setting: ChatSettingDefinition, value: string): void {
    const converted = setting.type === 'integer' ? Number.parseInt(value, 10) : setting.type === 'number' ? Number(value) : value;
    this.setValue(setting.key, converted);
  }
  resetValue(key: string): void { const settings = { ...this.draft.settings }; delete settings[key]; this.draft.settings = settings; if (Object.prototype.hasOwnProperty.call(this.originalSettings,key)) this.resetKeys.add(key); }
  resetAllValues(): void { Object.keys(this.originalSettings).forEach(key=>this.resetKeys.add(key)); this.draft.settings={}; }
  profileDefaults(): ChatSettingsMap { return Object.fromEntries(this.settings.map(item=>[item.key,item.scope_defaults['profile']??item.default])) as ChatSettingsMap; }
  setProcessGraph(graphId: string): void { this.draft.process_ref = graphId.trim() ? { graph_id: graphId.trim(), version: 'latest' } : null; }
  save(): void {
    const settings = this.selectedId ? Object.fromEntries([...Object.entries(this.draft.settings).filter(([key,value]) => this.originalSettings[key] !== value), ...[...this.resetKeys].map(key => [key, null])]) : { ...this.draft.settings };
    const payload = { ...this.draft, name: this.draft.name.trim(), settings };
    this.saving=true; const request = this.selectedId ? this.service.updateProfile(this.selectedId, payload) : this.service.createProfile(payload);
    request.subscribe({ next: profile => { this.saving=false; this.select(profile,true);if(this.afterSaveProfile){const target=this.afterSaveProfile;this.afterSaveProfile=null;this.select(target,true);} }, error: error => { this.saving=false; this.probeStatus=error?.error?.error || 'Speichern fehlgeschlagen; Entwurf bleibt erhalten'; } });
  }
  discoverModels(): void { this.probing=true; this.service.discoverProfileModels(this.draft.settings).subscribe({ next:r=>{this.probing=false;this.discoveredModels=r.models;this.probeStatus=`${r.models.length} Modelle gefunden`;}, error:e=>{this.probing=false;this.probeStatus=e?.error?.error_code || 'Discovery fehlgeschlagen';} }); }
  testConnection(): void { this.probing=true; this.service.testProfileConnection(this.draft.settings).subscribe({ next:r=>{this.probing=false;this.probeStatus=r.ok ? `Verbindung erfolgreich · Modell ${r.model_status}` : r.error_code || 'Verbindung fehlgeschlagen';}, error:e=>{this.probing=false;this.probeStatus=e?.error?.error_code || 'Verbindung fehlgeschlagen';} }); }
  loadPreview():void{this.service.previewProfile(this.selectedId||'general',this.draft.settings).subscribe({next:value=>this.previewJson=JSON.stringify(value,null,2),error:e=>this.probeStatus=e?.error?.error||'Vorschau fehlgeschlagen'});}
  copyPreview():void{void navigator.clipboard?.writeText(this.previewJson);}
  remove(): void { if (this.selectedId && confirm(`Profil „${this.draft.name}“ löschen?`)) { this.service.deleteProfile(this.selectedId); this.newProfile(); } }
  isDirty():boolean{return !!this.loadedSnapshot&&JSON.stringify(this.draft)!==this.loadedSnapshot;}
  discardSwitch():void{if(this.pendingProfile)this.select(this.pendingProfile,true);}
  saveThenSwitch():void{this.afterSaveProfile=this.pendingProfile;this.pendingProfile=null;this.save();}
  private emptyDraft(): { name: string; icon: string; description: string; system_prompt: string; settings: ChatSettingsMap; process_ref: { graph_id: string; version: string } | null } {
    return { name: '', icon: '🎯', description: '', system_prompt: '', settings: {}, process_ref: null };
  }
}
