import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatSettingDefinition, ChatSettingsMap, ChatSettingValue } from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-setting-controls', standalone: true, imports: [CommonModule, FormsModule],
  template: `
    <div class="toolbar"><input [(ngModel)]="search" placeholder="Einstellungen suchen" aria-label="Einstellungen suchen" />
      <label><input type="checkbox" [(ngModel)]="showAdvanced" /> Erweiterte Einstellungen</label>
      @if (!readOnly && overriddenCount) { <button (click)="resetAll.emit()">Alle Overrides zurücksetzen ({{ overriddenCount }})</button> }
    </div>
    @for (group of groups(); track group) {
      <details [open]="group !== 'Erweitert'" [hidden]="group === 'Erweitert' && !showAdvanced">
        <summary>{{ group }}</summary>
        @for (setting of fields(group); track setting.key) {
          <label class="field" [class.inherited]="!isOverride(setting.key)" [hidden]="!visible(setting)">
            <span>{{ setting.label }} <small>{{ isOverride(setting.key) ? overrideLabel : 'geerbt' }}</small></span>
            @if (setting.type === 'boolean') {
              <input type="checkbox" [ngModel]="value(setting)" (ngModelChange)="changed.emit({key:setting.key,value:$event})" [disabled]="readOnly" />
            } @else if (setting.allowed_values.length) {
              <select [ngModel]="value(setting)" (ngModelChange)="changed.emit({key:setting.key,value:$event})" [disabled]="readOnly">
                @for (option of setting.allowed_values; track option) { <option [ngValue]="option">{{ option }}</option> }
              </select>
            } @else {
              <input [type]="inputType(setting)" [ngModel]="value(setting)" [attr.min]="setting.constraints?.min"
                     [attr.max]="setting.constraints?.max" [attr.step]="setting.constraints?.step"
                     [attr.list]="setting.suggestions?.length ? 'suggestions-'+setting.key : null"
                     (ngModelChange)="changeTyped(setting,$event)" [disabled]="readOnly" />
              @if(setting.suggestions?.length){<datalist [id]="'suggestions-'+setting.key">@for(suggestion of setting.suggestions;track suggestion){<option [value]="suggestion"></option>}</datalist>}
            }
            @if (isOverride(setting.key) && !readOnly) { <button (click)="reset.emit(setting.key)" [attr.aria-label]="setting.label + ' auf Vererbung zurücksetzen'">↺</button> }
          </label>
        }
      </details>
    }
  `,
  styles: [`
    :host{display:grid;gap:7px}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.field{display:grid;grid-template-columns:minmax(180px,1fr) minmax(130px,240px) auto;gap:7px;align-items:center;padding:4px}
    .inherited{opacity:.72}small{opacity:.7}details{border-top:1px solid #1a2d4a;padding-top:5px}summary{cursor:pointer}@media(max-width:700px){.field{grid-template-columns:1fr auto auto}}
  `],
})
export class ChatSettingControlsComponent {
  @Input() settings: ChatSettingDefinition[] = [];
  @Input() scope: 'global'|'profile'|'session' = 'profile';
  @Input() delta: ChatSettingsMap = {};
  @Input() effective: ChatSettingsMap = {};
  @Input() readOnly = false;
  @Input() overrideLabel = 'überschrieben';
  @Output() changed = new EventEmitter<{key:string;value:ChatSettingValue}>();
  @Output() reset = new EventEmitter<string>();
  @Output() resetAll = new EventEmitter<void>();
  search=''; showAdvanced=false;
  get overriddenCount(): number { return Object.keys(this.delta).length; }
  groups(): string[] { return [...new Set(this.settings.filter(s=>s.scopes.includes(this.scope)).map(s=>s.advanced?'Erweitert':s.group))]; }
  fields(group:string): ChatSettingDefinition[] { const q=this.search.trim().toLowerCase(); return this.settings.filter(s=>s.scopes.includes(this.scope)&&(s.advanced?'Erweitert':s.group)===group&&(!q||`${s.key} ${s.label}`.toLowerCase().includes(q))); }
  isOverride(key:string): boolean { return Object.prototype.hasOwnProperty.call(this.delta,key); }
  value(setting:ChatSettingDefinition): unknown { return this.isOverride(setting.key)?this.delta[setting.key]:this.effective[setting.key]??setting.scope_defaults[this.scope]??setting.default; }
  visible(setting:ChatSettingDefinition): boolean { return Object.entries(setting.visible_when||{}).every(([key,values])=>values.includes(this.delta[key]??this.effective[key])); }
  inputType(setting:ChatSettingDefinition): string { return setting.type==='integer'||setting.type==='number'?'number':setting.key.endsWith('_api_base')?'url':setting.secret?'password':'text'; }
  changeTyped(setting:ChatSettingDefinition,value:string): void { this.changed.emit({key:setting.key,value:setting.type==='integer'?Number.parseInt(value,10):setting.type==='number'?Number(value):value}); }
}
