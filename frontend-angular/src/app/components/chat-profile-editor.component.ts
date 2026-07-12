import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  ChatProfile,
  ChatSessionsService,
  ChatSettingDefinition,
} from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-profile-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
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
          <div class="identity">
            <input [(ngModel)]="draft.icon" maxlength="4" [disabled]="readOnly" aria-label="Profil-Icon" />
            <input [(ngModel)]="draft.name" [disabled]="readOnly" placeholder="Profilname" aria-label="Profilname" />
          </div>
          <textarea [(ngModel)]="draft.system_prompt" [disabled]="readOnly" rows="3" placeholder="System-Prompt"></textarea>
          <label class="process-ref"><span>Visual Process <small>{{ draft.process_ref ? 'Profilbindung' : 'kein Prozess' }}</small></span>
            <input [ngModel]="draft.process_ref?.graph_id || ''" (ngModelChange)="setProcessGraph($event)" [disabled]="readOnly" placeholder="vp-graph-id" />
            @if (draft.process_ref && !readOnly) { <button (click)="setProcessGraph('')" title="Prozessbindung entfernen">↺</button> }
          </label>
          @for (group of groups; track group) {
            <details [open]="group !== 'Erweitert'">
              <summary>{{ group }}</summary>
              @for (setting of settingsFor(group); track setting.key) {
                <label class="setting" [class.inherited]="!hasOverride(setting.key)">
                  <span>{{ setting.label }} <small>{{ hasOverride(setting.key) ? 'Profilwert' : 'Standardwert' }}</small></span>
                  @if (setting.type === 'boolean') {
                    <input type="checkbox" [ngModel]="effective(setting)" (ngModelChange)="setValue(setting.key, $event)" [disabled]="readOnly" />
                  } @else if (setting.allowed_values.length) {
                    <select [ngModel]="effective(setting)" (ngModelChange)="setValue(setting.key, $event)" [disabled]="readOnly">
                      @for (option of setting.allowed_values; track option) { <option [ngValue]="option">{{ option }}</option> }
                    </select>
                  } @else {
                    <input [type]="setting.secret ? 'password' : 'text'" [ngModel]="effective(setting)"
                           (ngModelChange)="setTypedValue(setting, $event)" [disabled]="readOnly" />
                  }
                  @if (hasOverride(setting.key) && !readOnly) {
                    <button title="Auf Standardwert zurücksetzen" (click)="resetValue(setting.key)">↺</button>
                  }
                </label>
              }
            </details>
          }
          <footer>
            @if (readOnly) {
              <button (click)="duplicate()">Als eigenes Profil duplizieren</button>
            } @else {
              <button (click)="save()" [disabled]="!draft.name.trim()">Speichern</button>
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
  draft = this.emptyDraft();

  get groups(): string[] { return [...new Set(this.settings.map(item => item.advanced ? 'Erweitert' : item.group))]; }

  ngOnInit(): void {
    this.subscriptions.push(
      this.service.profiles$.subscribe(profiles => { this.profiles = profiles; }),
      this.service.settingSchema$.subscribe(schema => { this.settings = schema.settings.filter(item => item.scopes.includes('profile')); }),
    );
  }
  ngOnDestroy(): void { this.subscriptions.forEach(subscription => subscription.unsubscribe()); }
  settingsFor(group: string): ChatSettingDefinition[] { return this.settings.filter(item => (item.advanced ? 'Erweitert' : item.group) === group); }
  select(profile: ChatProfile): void {
    this.selectedId = profile.id; this.readOnly = profile.builtin;
    this.draft = { name: profile.name, icon: profile.icon || '🎯', system_prompt: profile.system_prompt || '', settings: { ...profile.settings }, process_ref: profile.process_ref ? { ...profile.process_ref } : null };
  }
  newProfile(): void { this.selectedId = ''; this.readOnly = false; this.draft = this.emptyDraft(); }
  duplicate(): void { this.selectedId = ''; this.readOnly = false; this.draft = { ...this.draft, name: `${this.draft.name} (Kopie)`, settings: { ...this.draft.settings } }; }
  cancel(): void { const profile = this.profiles.find(item => item.id === this.selectedId); profile ? this.select(profile) : this.newProfile(); }
  hasOverride(key: string): boolean { return Object.prototype.hasOwnProperty.call(this.draft.settings, key); }
  effective(setting: ChatSettingDefinition): unknown { return this.hasOverride(setting.key) ? this.draft.settings[setting.key] : setting.scope_defaults['profile'] ?? setting.default; }
  setValue(key: string, value: unknown): void { this.draft.settings = { ...this.draft.settings, [key]: value }; }
  setTypedValue(setting: ChatSettingDefinition, value: string): void {
    const converted = setting.type === 'integer' ? Number.parseInt(value, 10) : setting.type === 'number' ? Number(value) : value;
    this.setValue(setting.key, converted);
  }
  resetValue(key: string): void { const settings = { ...this.draft.settings }; delete settings[key]; this.draft.settings = settings; }
  setProcessGraph(graphId: string): void { this.draft.process_ref = graphId.trim() ? { graph_id: graphId.trim(), version: 'latest' } : null; }
  save(): void {
    const payload = { ...this.draft, name: this.draft.name.trim(), settings: { ...this.draft.settings } };
    this.selectedId ? this.service.updateProfile(this.selectedId, payload) : this.service.createProfile(payload);
  }
  remove(): void { if (this.selectedId && confirm(`Profil „${this.draft.name}“ löschen?`)) { this.service.deleteProfile(this.selectedId); this.newProfile(); } }
  private emptyDraft(): { name: string; icon: string; system_prompt: string; settings: Record<string, unknown>; process_ref: { graph_id: string; version: string } | null } {
    return { name: '', icon: '🎯', system_prompt: '', settings: {}, process_ref: null };
  }
}
