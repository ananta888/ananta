import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { AiSnakeConfigService } from '../services/ai-snake-config.service';
import { ChatSessionsService } from '../services/chat-sessions.service';
import { DomainScopeService } from '../features/codecompass-graph/services/domain-scope.service';
import { DomainScopePanelComponent } from '../features/codecompass-graph/components/domain-scope-panel/domain-scope-panel.component';

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
  { key: 'chat_include_task_memory', label: 'Task-Memory', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_include_runtime_status', label: 'TUI-Status in Prompt', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_source_pack_id', label: 'Source Pack', group: 'Kontext / RAG', type: 'choice',
    options: ['ananta-dev-default', 'ananta-default', 'ananta-local-only'] },
  { key: 'chat_context_chars', label: 'Context Chars', group: 'Kontext / RAG', type: 'choice',
    options: ['1000', '2000', '3000', '5000', '8000', '12000'] },
  { key: 'chat_rag_top_k', label: 'RAG Top-K', group: 'Kontext / RAG', type: 'choice',
    options: ['12', '24', '32', '48', '64', '96', '120'] },
  { key: 'chat_retrieval_profile', label: 'Retrieval Profile', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', 'repo_first', 'docs_first', 'legacy'] },
  { key: 'chat_codecompass_trigger_mode', label: 'CodeCompass Trigger', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', 'force_codecompass', 'force_repo_first', 'disabled'] },
  { key: 'chat_architecture_analysis_mode', label: 'Architektur Analyse', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', 'rag_iterative', 'standard', 'full_scan', 'off'] },
  { key: 'rag_iterative_tool_calls_enabled', label: 'RAG-Iterativ: Tool-Calls', group: 'Kontext / RAG', type: 'bool' },
  { key: 'rag_iterative_max_tool_calls', label: 'RAG-Iterativ: Max. Tool-Calls (0=∞)', group: 'Kontext / RAG', type: 'choice',
    options: ['0', '1', '2', '3', '4', '6', '8', '10', '20', '50'] },
  { key: 'rag_iterative_import_depth', label: 'RAG-Iterativ: Import-Tiefe', group: 'Kontext / RAG', type: 'choice',
    options: ['0', '1', '2', '3'] },
  { key: 'rag_iterative_symbol_expand_max', label: 'RAG-Iterativ: Symbol-Graph Expansion (0=aus)', group: 'Kontext / RAG', type: 'choice',
    options: ['0', '5', '10', '15', '20', '30'] },
  { key: 'rag_iterative_catalog_chars', label: 'RAG-Iterativ: Katalog-Zeichen (component-catalog.md)', group: 'Kontext / RAG', type: 'choice',
    options: ['5000', '10000', '20000', '30000', '40000', '60000'] },
  { key: 'rag_iterative_tool_chars_per_file', label: 'RAG-Iterativ: Zeichen pro Datei (Tool-Call)', group: 'Kontext / RAG', type: 'choice',
    options: ['4000', '8000', '15000', '20000', '40000', '80000'] },
  { key: 'rag_iterative_initial_min_files', label: 'RAG-Iterativ: Initiale Mindest-Dateien', group: 'Kontext / RAG', type: 'choice',
    options: ['0', '1', '2', '3', '4', '5'] },
  { key: 'rag_iterative_initial_max_files', label: 'RAG-Iterativ: Initiale Max-Dateien', group: 'Kontext / RAG', type: 'choice',
    options: ['3', '5', '8', '10', '12', '16'] },
  { key: 'rag_iterative_summarize_reads', label: 'RAG-Iterativ: Zwischen-Zusammenfassung nach Dateilesen', group: 'Kontext / RAG', type: 'bool' },
  { key: 'rag_iterative_summary_chars', label: 'RAG-Iterativ: Zusammenfassung Max-Zeichen', group: 'Kontext / RAG', type: 'choice',
    options: ['200', '400', '600', '800', '1200', '2000'] },
  { key: 'chat_full_scan_source_only', label: 'Full-Scan: Nur Quellcode', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_full_scan_max_batches', label: 'Full-Scan: Max. Batches', group: 'Kontext / RAG', type: 'choice',
    options: ['2', '4', '6', '8', '12', '16'] },
  { key: 'chat_full_scan_files_per_batch', label: 'Full-Scan: Dateien/Batch', group: 'Kontext / RAG', type: 'choice',
    options: ['1', '2', '3', '5', '8'] },
  { key: 'chat_full_scan_parallel_batches', label: 'Full-Scan: Parallele Batches', group: 'Kontext / RAG', type: 'choice',
    options: ['1', '2', '3', '4', '6', '8'] },
  { key: 'chat_full_scan_timeout_s', label: 'Full-Scan: Timeout (s)', group: 'Kontext / RAG', type: 'choice',
    options: ['300', '600', '900', '1200', '1800', '3600'] },
  { key: 'chat_full_scan_chars_per_file', label: 'Zeichen/Datei (Full-Scan & RAG)', group: 'Kontext / RAG', type: 'choice',
    options: ['300', '600', '1200', '2000', '3500', '4000', '6000', '8000'] },
  { key: 'chat_full_scan_max_input_tokens', label: 'Full-Scan: Max. Input-Tokens', group: 'Kontext / RAG', type: 'choice',
    options: ['auto', '1500', '3000', '6000', '12000', '24000'] },
  { key: 'chat_retrieval_domain_hint', label: 'Retrieval Domain Hint', group: 'Kontext / RAG', type: 'choice',
    options: ['', 'codecompass', 'ai_snake', 'worker', 'ananta_game', 'operator_tui', 'ops', 'generic'] },
  { key: 'chat_code_questions_repo_first', label: 'Codefragen Repo-first', group: 'Kontext / RAG', type: 'bool' },
  { key: 'chat_max_tokens', label: 'Max Tokens', group: 'Kontext / RAG', type: 'choice',
    options: ['400', '800', '1200', '2000', '4000', '8000'] },
  { key: 'chat_answer_chars', label: 'Antwort Chars', group: 'Kontext / RAG', type: 'choice',
    options: ['600', '1200', '2400', '4000', '6000', '8000', '12000', '16000', '24000'] },
  { key: 'chat_answer_overflow_policy', label: 'Antwort Überlänge', group: 'Kontext / RAG', type: 'choice',
    options: ['allow', 'summarize', 'truncate'] },
  { key: 'chat_never_truncate_answers', label: 'Nie hart kürzen', group: 'Kontext / RAG', type: 'bool' },
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
  // Embedding / Reranking
  { key: 'embedding_model_id', label: 'Embedding-Modell', group: 'Embedding / Reranking', type: 'choice',
    options: ['paraphrase-multilingual-MiniLM-L12-v2', 'all-MiniLM-L6-v2', 'deepset/gbert-base', 'intfloat/multilingual-e5-small'] },
  { key: 'embedding_lang_detect', label: 'Sprach-Erkennung (langdetect)', group: 'Embedding / Reranking', type: 'bool' },
  { key: 'embedding_lang_model_de', label: 'Modell für Deutsch', group: 'Embedding / Reranking', type: 'choice',
    options: ['paraphrase-multilingual-MiniLM-L12-v2', 'deepset/gbert-base', 'intfloat/multilingual-e5-small'] },
  { key: 'embedding_lang_model_en', label: 'Modell für Englisch', group: 'Embedding / Reranking', type: 'choice',
    options: ['all-MiniLM-L6-v2', 'paraphrase-multilingual-MiniLM-L12-v2', 'intfloat/multilingual-e5-small'] },
  // Query-Reform / Intent-Extraktion
  { key: 'query_reform_mode', label: 'Query-Reform Modus', group: 'Query-Reform', type: 'choice',
    options: ['off', 'regex', 'regex_embed', 'llm'] },
  { key: 'query_reform_llm_backend', label: 'LLM Backend (Modus: llm)', group: 'Query-Reform', type: 'choice',
    options: ['ananta-worker', 'opencode', 'lmstudio', 'hermes'] },
  { key: 'query_reform_llm_model', label: 'LLM Modell (Modus: llm)', group: 'Query-Reform', type: 'text' },
];

const PUG_PRESET_QUIET = {
  predictive_guide_enabled: true, predictive_guide_mode: 'quiet',
  predictive_guide_dwell_ms: 3000, predictive_guide_min_confidence: 0.75,
  predictive_guide_ttl_seconds: 10, predictive_guide_multi_candidates: 1,
  predictive_guide_log_deltas_only: true,
};
const PUG_PRESET_BALANCED = {
  predictive_guide_enabled: true, predictive_guide_mode: 'balanced',
  predictive_guide_dwell_ms: 1500, predictive_guide_min_confidence: 0.55,
  predictive_guide_ttl_seconds: 20, predictive_guide_multi_candidates: 3,
  predictive_guide_log_deltas_only: true,
};
const PUG_PRESET_EAGER = {
  predictive_guide_enabled: true, predictive_guide_mode: 'eager',
  predictive_guide_dwell_ms: 500, predictive_guide_min_confidence: 0.35,
  predictive_guide_ttl_seconds: 30, predictive_guide_multi_candidates: 4,
  predictive_guide_log_deltas_only: false,
};

@Component({
  selector: 'app-ai-snake-config-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, DomainScopePanelComponent],
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
                  @if (field.key === 'chat_backend_model') {
                    <div class="cfg-model-row">
                      <input class="cfg-input cfg-model-input" type="text"
                        [value]="getStr(field.key)"
                        list="cfg-model-datalist"
                        (change)="setStr(field.key, $any($event.target).value)">
                      <datalist id="cfg-model-datalist">
                        @for (m of modelsList; track m) {
                          <option [value]="m">{{ m }}</option>
                        }
                      </datalist>
                      <button class="cfg-reload-btn" (click)="loadModels()" [disabled]="modelsLoading" title="Modelle neu laden">
                        {{ modelsLoading ? '…' : '↻' }}
                      </button>
                    </div>
                  } @else {
                    <input class="cfg-input" type="text" [value]="getStr(field.key)" (change)="setStr(field.key, $any($event.target).value)">
                  }
                }
              </div>
            }
          </div>
        }
        <!-- CCRDS-015: runtime domain scope selection (CodeCompass) -->
        <div class="cfg-group">
          <div class="cfg-group-title">Domain-Scope (CodeCompass)</div>
          <div class="cfg-row">
            <span class="cfg-label">Erkannte Domains anzeigen</span>
            <button class="cfg-scope-toggle" (click)="toggleDomainScope()">
              {{ showDomainScope ? 'Ausblenden' : 'Anzeigen' }}
            </button>
          </div>
          @if (showDomainScope) {
            <app-domain-scope-panel />
          }
        </div>
        <!-- Predictive Guide (PUG) — session-scoped settings on ananta-visual -->
        <div class="cfg-group">
          <div class="cfg-group-title">Predictive Guide</div>
          <!-- Preset buttons -->
          <div class="cfg-row pug-presets">
            <span class="cfg-label">Preset</span>
            <div class="pug-preset-row">
              @for (preset of pugPresets; track preset.key) {
                <button class="pug-preset-btn"
                  [class.active]="getPugStr('predictive_guide_mode') === preset.mode"
                  (click)="applyPugPreset(preset.key)">{{ preset.label }}</button>
              }
            </div>
          </div>
          <!-- Individual settings -->
          <div class="cfg-row">
            <span class="cfg-label">Aktiv</span>
            <label class="cfg-toggle">
              <input type="checkbox" [checked]="getPugBool('predictive_guide_enabled')"
                (change)="setPugBool('predictive_guide_enabled', $any($event.target).checked)">
              <span class="cfg-toggle-track"></span>
            </label>
          </div>
          <div class="cfg-row">
            <span class="cfg-label">Hover-Dwell (ms)</span>
            <select class="cfg-select" [value]="getPugStr('predictive_guide_dwell_ms')"
              (change)="setPugNum('predictive_guide_dwell_ms', +$any($event.target).value)">
              @for (opt of ['500','1000','1500','2000','3000','5000']; track opt) {
                <option [value]="opt" [selected]="getPugStr('predictive_guide_dwell_ms') === opt">{{ opt }}</option>
              }
            </select>
          </div>
          <div class="cfg-row">
            <span class="cfg-label">Min-Confidence</span>
            <select class="cfg-select" [value]="getPugStr('predictive_guide_min_confidence')"
              (change)="setPugNum('predictive_guide_min_confidence', +$any($event.target).value)">
              @for (opt of ['0.1','0.2','0.35','0.45','0.55','0.65','0.75','0.9']; track opt) {
                <option [value]="opt" [selected]="getPugStr('predictive_guide_min_confidence') === opt">{{ opt }}</option>
              }
            </select>
          </div>
          <div class="cfg-row">
            <span class="cfg-label">TTL (s)</span>
            <select class="cfg-select" [value]="getPugStr('predictive_guide_ttl_seconds')"
              (change)="setPugNum('predictive_guide_ttl_seconds', +$any($event.target).value)">
              @for (opt of ['5','10','20','30','45','60']; track opt) {
                <option [value]="opt" [selected]="getPugStr('predictive_guide_ttl_seconds') === opt">{{ opt }}</option>
              }
            </select>
          </div>
          <div class="cfg-row">
            <span class="cfg-label">Multi-Kandidaten</span>
            <select class="cfg-select" [value]="getPugStr('predictive_guide_multi_candidates')"
              (change)="setPugNum('predictive_guide_multi_candidates', +$any($event.target).value)">
              @for (opt of ['1','2','3','4','5']; track opt) {
                <option [value]="opt" [selected]="getPugStr('predictive_guide_multi_candidates') === opt">{{ opt }}</option>
              }
            </select>
          </div>
          <div class="cfg-row">
            <span class="cfg-label">Nur Delta-Log</span>
            <label class="cfg-toggle">
              <input type="checkbox" [checked]="getPugBool('predictive_guide_log_deltas_only')"
                (change)="setPugBool('predictive_guide_log_deltas_only', $any($event.target).checked)">
              <span class="cfg-toggle-track"></span>
            </label>
          </div>
        </div>
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
    .cfg-model-row { display: flex; align-items: center; gap: 4px; }
    .cfg-model-input { width: 130px; }
    .cfg-reload-btn {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #7fffd4;
      font-size: 13px; font-family: inherit; padding: 1px 6px; border-radius: 2px;
      cursor: pointer; flex-shrink: 0; line-height: 1;
    }
    .cfg-reload-btn:hover:not(:disabled) { border-color: #2a4d7a; background: #131e36; }
    .cfg-reload-btn:disabled { opacity: 0.4; cursor: default; }
    .cfg-scope-toggle {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      font-size: 11px; font-family: inherit; padding: 2px 8px; border-radius: 2px; cursor: pointer;
    }
    .cfg-scope-toggle:hover { border-color: #2a4d7a; }
    .pug-presets { align-items: flex-start; padding-top: 6px; }
    .pug-preset-row { display: flex; gap: 4px; flex-wrap: wrap; }
    .pug-preset-btn {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      font-size: 10px; font-family: inherit; padding: 2px 8px; border-radius: 2px; cursor: pointer;
    }
    .pug-preset-btn:hover { border-color: #2a4d7a; }
    .pug-preset-btn.active { border-color: #7fffd4; color: #7fffd4; }
  `],
})
export class AiSnakeConfigPanelComponent implements OnInit, OnDestroy {
  private svc = inject(AiSnakeConfigService);
  private sessions = inject(ChatSessionsService);
  private domainScope = inject(DomainScopeService);

  search = '';
  showDomainScope = false;
  modelsList: string[] = [];
  modelsLoading = false;
  private _filtered: ConfigField[] = [...FIELDS];
  private _sub?: Subscription;

  readonly pugPresets = [
    { key: 'quiet',    label: 'Quiet',    mode: 'quiet'    },
    { key: 'balanced', label: 'Balanced', mode: 'balanced' },
    { key: 'eager',    label: 'Eager',    mode: 'eager'    },
  ];

  ngOnInit(): void {
    this.svc.load();
    this.loadModels();
    this.sessions.load();
  }

  ngOnDestroy(): void {
    this._sub?.unsubscribe();
  }

  loadModels(): void {
    if (this.modelsLoading) return;
    this.modelsLoading = true;
    this.svc.listModels().subscribe({
      next: models => { this.modelsList = models; this.modelsLoading = false; },
      error: () => { this.modelsLoading = false; },
    });
  }

  toggleDomainScope(): void {
    this.showDomainScope = !this.showDomainScope;
    if (this.showDomainScope) this.domainScope.loadDomains();
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

  // ── Predictive Guide (PUG) — session-scoped on ananta-visual ──────────────

  private get pugSettings(): Record<string, unknown> {
    return this.sessions.sessions$.value.find(s => s.id === 'ananta-visual')?.settings ?? {};
  }

  getPugBool(key: string): boolean {
    const v = this.pugSettings[key];
    return v === undefined ? false : Boolean(v);
  }

  getPugStr(key: string): string {
    const v = this.pugSettings[key];
    return v === undefined ? '' : String(v);
  }

  setPugBool(key: string, value: boolean): void {
    this.sessions.patchSetting('ananta-visual', key, value);
    this.sessions.patchSetting('ananta-visual', 'predictive_guide_mode', 'custom');
  }

  setPugNum(key: string, value: number): void {
    this.sessions.patchSetting('ananta-visual', key, value);
    this.sessions.patchSetting('ananta-visual', 'predictive_guide_mode', 'custom');
  }

  applyPugPreset(presetKey: string): void {
    const map: Record<string, Record<string, unknown>> = {
      quiet:    PUG_PRESET_QUIET,
      balanced: PUG_PRESET_BALANCED,
      eager:    PUG_PRESET_EAGER,
    };
    const preset = map[presetKey];
    if (!preset) return;
    for (const [k, v] of Object.entries(preset)) {
      this.sessions.patchSetting('ananta-visual', k, v);
    }
  }
}
