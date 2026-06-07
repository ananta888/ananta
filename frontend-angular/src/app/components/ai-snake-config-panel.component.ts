import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AiSnakeConfigService } from '../services/ai-snake-config.service';

interface ConfigField {
  key: string;
  label: string;
  group: string;
  type: 'bool' | 'choice' | 'text';
  options?: string[];
}

const FIELDS: ConfigField[] = [
  // Visual
  { key: 'tutorial_mode', label: 'Tutorial AI-Snake', group: 'Visual', type: 'bool' },
  { key: 'ai_snake_provider_preference', label: 'Visual Provider', group: 'Visual', type: 'choice',
    options: ['lmstudio', 'opencode', 'hermes', 'worker-propose'] },
  { key: 'ai_visual_use_codecompass', label: 'Visual CodeCompass', group: 'Visual', type: 'bool' },
  { key: 'chat_panel_open', label: 'Chat Panel offen', group: 'Visual', type: 'bool' },
  // Chat Backend
  { key: 'chat_backend', label: 'Chat Provider', group: 'Chat Backend', type: 'choice',
    options: ['ananta-worker', 'opencode', 'lmstudio', 'hermes'] },
  { key: 'chat_backend_model', label: 'Chat Model', group: 'Chat Backend', type: 'text' },
  { key: 'chat_backend_api_base', label: 'Chat API Base', group: 'Chat Backend', type: 'choice',
    options: ['http://localhost:1234/v1', 'http://localhost:8080/v1', 'http://localhost:11434/v1'] },
  { key: 'chat_ask_timeout_s', label: 'Timeout (s)', group: 'Chat Backend', type: 'choice',
    options: ['20', '30', '45', '60', '90', '120', '180', '300', '600', '1200', '1800'] },
  { key: 'chat_backend_fallback', label: 'Fallback', group: 'Chat Backend', type: 'choice',
    options: ['none', 'lmstudio', 'local_knowledge'] },
  { key: 'chat_worker_mode', label: 'Worker Modus', group: 'Chat Backend', type: 'choice',
    options: ['snake_ask', 'propose', 'auto'] },
  { key: 'chat_pass_memory_to_worker', label: 'Memory an Worker', group: 'Chat Backend', type: 'bool' },
  // Context / RAG
  { key: 'chat_use_codecompass', label: 'CodeCompass', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_include_local_project', label: 'Lokales Projekt', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_include_wikipedia', label: 'Wikipedia', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_include_runtime_status', label: 'TUI-Status in Prompt', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_source_pack_id', label: 'Source Pack', group: 'Kontext / RAG', type: 'choice',
    options: ['ananta-dev-default', 'ananta-default', 'ananta-local-only'] },
  { key: 'chat_context_chars', label: 'Context Chars', group: 'Kontext / RAG', type: 'choice',
    options: ['1000', '2000', '3000', '5000', '8000', '12000'] },
  { key: 'chat_rag_top_k', label: 'RAG Top-K', group: 'Kontext / RAG', type: 'choice',
    options: ['12', '24', '32', '48', '64', '96', '120'] },
  { key: 'chat_retrieval_profile', label: 'Retrieval Profile', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', 'repo_first', 'docs_first', 'legacy'] },
  { key: 'chat_architecture_analysis_mode', label: 'Architektur Analyse', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', 'standard', 'full_scan', 'off'] },
  { key: 'chat_full_scan_source_only', label: 'Full-Scan: Nur Quellcode', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_full_scan_max_batches', label: 'Full-Scan: Max. Batches', group: 'Kontext / RAG', type: 'choice',
    options: ['2', '4', '6', '8', '12', '16'] },
  { key: 'chat_full_scan_files_per_batch', label: 'Full-Scan: Dateien/Batch', group: 'Kontext / RAG', type: 'choice',
    options: ['1', '2', '3', '5', '8'] },
  { key: 'chat_full_scan_parallel_batches', label: 'Full-Scan: Parallele Batches', group: 'Kontext / RAG', type: 'choice',
    options: ['1', '2', '3', '4', '6', '8'] },
  { key: 'chat_full_scan_timeout_s', label: 'Full-Scan: Timeout (s)', group: 'Kontext / RAG', type: 'choice',
    options: ['300', '600', '900', '1200', '1800', '3600'] },
  { key: 'chat_retrieval_domain_hint', label: 'Retrieval Domain Hint', group: 'Kontext / RAG', type: 'text' },
  { key: 'chat_code_questions_repo_first', label: 'Codefragen Repo-first', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_max_tokens', label: 'Max Tokens', group: 'Kontext / RAG', type: 'choice',
    options: ['400', '800', '1200', '2000', '4000', '8000'] },
  { key: 'chat_answer_chars', label: 'Antwort Chars', group: 'Kontext / RAG', type: 'choice',
    options: ['600', '1200', '2400', '4000', '6000', '8000', '12000'] },
  // Memory
  { key: 'chat_use_history', label: 'Verlauf nutzen', group: 'Chat Memory', type: 'bool' },
  { key: 'chat_history_turns', label: 'History Turns', group: 'Chat Memory', type: 'choice',
    options: ['3', '6', '10', '15', '20', '30'] },
  { key: 'chat_history_chars', label: 'History Chars', group: 'Chat Memory', type: 'choice',
    options: ['600', '1200', '1800', '3000', '5000'] },
  { key: 'chat_use_summary', label: 'Zusammenfassung', group: 'Chat Memory', type: 'bool' },
  { key: 'chat_summary_chars', label: 'Summary Chars', group: 'Chat Memory', type: 'choice',
    options: ['500', '1000', '1500', '2500', '4000'] },
  { key: 'chat_summary_update_every_turns', label: 'Summary alle N Turns', group: 'Chat Memory', type: 'choice',
    options: ['1', '2', '3', '5', '10'] },
  // Input History
  { key: 'input_history_chat_enabled', label: 'Chat-Eingaben speichern', group: 'Input-Verlauf', type: 'bool' },
  { key: 'input_history_command_enabled', label: 'Befehle speichern', group: 'Input-Verlauf', type: 'bool' },
  { key: 'input_history_max_entries', label: 'Max. Einträge', group: 'Input-Verlauf', type: 'choice',
    options: ['20', '50', '100', '200', '500'] },
];

@Component({
  selector: 'app-ai-snake-config-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="cfg-panel">
      <div class="cfg-header">
        <span>⚙ AI-Snake Konfiguration</span>
        <input class="cfg-search" [(ngModel)]="search" placeholder="Suchen..." (ngModelChange)="updateFiltered()">
      </div>
      <div class="cfg-body">
        @for (group of visibleGroups(); track group) {
          <div class="cfg-group">
            <div class="cfg-group-title">{{ group }}</div>
            @for (field of filteredFields(group); track field.key) {
              <div class="cfg-row">
                <span class="cfg-label">{{ field.label }}</span>
                @if (field.type === 'bool') {
                  <label class="cfg-toggle">
                    <input type="checkbox" [checked]="getBool(field.key)" (change)="setBool(field.key, $any($event.target).checked)">
                    <span class="cfg-toggle-track"></span>
                  </label>
                } @else if (field.type === 'choice') {
                  @if (field.key === 'chat_backend_api_base') {
                    <input
                      class="cfg-input"
                      type="text"
                      [value]="getStr(field.key)"
                      [attr.list]="'opts-' + field.key"
                      (change)="setStr(field.key, $any($event.target).value)" />
                    <datalist [id]="'opts-' + field.key">
                      @for (opt of getOptions(field); track opt) {
                        <option [value]="opt">{{ opt }}</option>
                      }
                    </datalist>
                  } @else {
                    <select class="cfg-select" [value]="getStr(field.key)" (change)="setStr(field.key, $any($event.target).value)">
                      @for (opt of getOptions(field); track opt) {
                        <option [value]="opt" [selected]="getStr(field.key) === opt">{{ opt }}</option>
                      }
                    </select>
                  }
                } @else {
                  <input class="cfg-input" type="text" [value]="getStr(field.key)" (change)="setStr(field.key, $any($event.target).value)">
                }
              </div>
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    :host { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .cfg-panel { display: flex; flex-direction: column; height: 100%; background: #0b1220; color: #c8d8f8; }
    .cfg-header {
      padding: 8px 10px; border-bottom: 1px solid #1a2d4a; background: #0d1828;
      display: flex; align-items: center; gap: 8px; flex-shrink: 0; font-size: 12px; font-weight: 600;
    }
    .cfg-search {
      flex: 1; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 3px 7px; font-size: 11px; font-family: inherit; border-radius: 2px;
    }
    .cfg-body { flex: 1; overflow-y: auto; padding: 6px 8px; }
    .cfg-body::-webkit-scrollbar { width: 4px; }
    .cfg-body::-webkit-scrollbar-thumb { background: #1a2d4a; }
    .cfg-group { margin-bottom: 10px; }
    .cfg-group-title { font-size: 10px; color: #4a6a9a; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; padding-bottom: 2px; border-bottom: 1px solid #131e36; }
    .cfg-row { display: flex; align-items: center; justify-content: space-between; padding: 3px 0; min-height: 26px; border-bottom: 1px solid #0f1828; }
    .cfg-label { font-size: 11px; color: #a8c7ff; flex: 1; }
    .cfg-select {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      font-size: 11px; font-family: inherit; padding: 2px 5px; border-radius: 2px; max-width: 160px;
    }
    .cfg-input {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      font-size: 11px; font-family: inherit; padding: 2px 5px; border-radius: 2px; width: 160px;
    }
    .cfg-toggle { position: relative; display: inline-flex; cursor: pointer; }
    .cfg-toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
    .cfg-toggle-track {
      width: 28px; height: 14px; background: #1a2d4a; border-radius: 7px; display: block;
      transition: background 0.2s;
    }
    .cfg-toggle input:checked + .cfg-toggle-track { background: #7fffd4; }
    .cfg-toggle-track::after {
      content: ''; position: absolute; top: 2px; left: 2px;
      width: 10px; height: 10px; background: #6b8ab8; border-radius: 50%; transition: left 0.2s;
    }
    .cfg-toggle input:checked ~ .cfg-toggle-track::after { left: 16px; background: #0b1220; }
  `],
})
export class AiSnakeConfigPanelComponent implements OnInit {
  private svc = inject(AiSnakeConfigService);

  search = '';
  private _filtered: ConfigField[] = [...FIELDS];

  ngOnInit(): void {
    this.svc.load();
  }

  updateFiltered(): void {
    const q = this.search.toLowerCase().trim();
    this._filtered = q ? FIELDS.filter(f => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q) || f.group.toLowerCase().includes(q)) : [...FIELDS];
  }

  visibleGroups(): string[] {
    return [...new Set(this._filtered.map(f => f.group))];
  }

  filteredFields(group: string): ConfigField[] {
    return this._filtered.filter(f => f.group === group);
  }

  getOptions(field: ConfigField): string[] {
    const opts = this.svc.options$.value?.options[field.key];
    const current = this.getStr(field.key);
    const base = opts ?? field.options ?? [];
    return current && !base.includes(current) ? [current, ...base] : base;
  }

  getBool(key: string): boolean {
    return !!this.svc.config$.value[key];
  }

  getStr(key: string): string {
    return String(this.svc.config$.value[key] ?? '');
  }

  setBool(key: string, value: boolean): void {
    this.svc.updateField(key, value);
  }

  setStr(key: string, value: string): void {
    this.svc.updateField(key, value);
  }
}
