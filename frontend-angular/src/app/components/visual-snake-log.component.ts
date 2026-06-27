/**
 * VisualSnakeLogComponent — read-only timeline view for the ananta-visual
 * chat session. Renders incoming [ui-tick] snapshots as compact cards and
 * the visual guide's reply as a normal chat bubble, grouped chronologically.
 *
 * Used in the AI-Snake chat panel when the active session is ananta-visual.
 */
import { Component, inject, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';

import { Subscription } from 'rxjs';
import { ChatHistoryService, ChatHistoryMessage } from '../services/chat-history.service';

interface GuideCandidate {
  label: string;    // "primary", "alt-1", …
  bubble: string;   // short guide sentence
  steps: Array<{ waypoint: string; bubble: string; delay_ms: number }>;
}

interface UiTickEntry {
  id: string;
  ts: number;
  text: string;            // full [ui-tick] ... text
  kind: 'tick' | 'delta' | 'explain';
  route: string;            // first path-like token in the snapshot
  snapshot: string;         // full compact snapshot
  preview: string;          // first 220 chars of the snapshot, formatted
  replyId?: string;         // id of the AI reply that followed, if any
  replyText?: string;       // primary bubble (stripped of __CANDIDATES__/__GUIDE__)
  candidates?: GuideCandidate[];  // full multi-candidate list (when n_candidates > 1)
  showCandidates?: boolean;       // UI toggle for the alternatives accordion
}

type FilterKind = 'all' | 'tick' | 'delta' | 'explain' | 'guide';

@Component({
  selector: 'app-visual-snake-log',
  standalone: true,
  imports: [],
  template: `
    <div class="vlog">
      <div class="vlog-head">
        <span>Snake Log</span>
        <div class="filter-bar">
          <button class="fbtn" [class.active]="activeFilter==='all'"     (click)="setFilter('all')">Alle</button>
          <button class="fbtn" [class.active]="activeFilter==='tick'"    (click)="setFilter('tick')">[ui-tick]</button>
          <button class="fbtn" [class.active]="activeFilter==='delta'"   (click)="setFilter('delta')">[ui-delta]</button>
          <button class="fbtn" [class.active]="activeFilter==='explain'" (click)="setFilter('explain')">[explain]</button>
          <button class="fbtn" [class.active]="activeFilter==='guide'"   (click)="setFilter('guide')">Guide</button>
        </div>
        <button class="ghost" (click)="refresh()" title="Neu laden">↻</button>
      </div>
      @if (filteredEntries().length) {
        <div class="vlist">
          @for (e of filteredEntries(); track e.id) {
            <div class="vitem" [class.has-reply]="!!e.replyId" [class.is-explain]="e.kind === 'explain'" [class.is-delta]="e.kind === 'delta'">
              <div class="vtime">
                {{ formatTime(e.ts) }}
                @if (e.kind === 'explain') {
                  <span class="vkind">Erklären</span>
                }
                @if (e.kind === 'delta') {
                  <span class="vkind delta">delta</span>
                }
                <button class="export-btn" (click)="downloadDebugExport(e)" title="Export">⬇</button>
              </div>
              @if (e.route) {
                <div class="vroute">{{ e.route }}</div>
              }
              <div class="vsnap" [class.delta-snap]="e.kind === 'delta'" [title]="e.snapshot">{{ e.preview }}</div>
              @if (e.replyText) {
                <div class="vreply">
                  <span class="vreply-label">Snake →</span>
                  <span class="vreply-body">{{ e.replyText }}</span>
                </div>
              }
              @if (e.candidates && e.candidates.length > 1) {
                <div class="vcand-toggle" (click)="e.showCandidates = !e.showCandidates">
                  {{ e.showCandidates ? '▲' : '▼' }} {{ e.candidates.length - 1 }} Alternativen
                </div>
                @if (e.showCandidates) {
                  <div class="vcand-list">
                    @for (c of e.candidates.slice(1); track c.label) {
                      <div class="vcand-item">
                        <span class="vcand-label">{{ c.label }}</span>
                        <span class="vcand-bubble">{{ c.bubble }}</span>
                      </div>
                    }
                  </div>
                }
              }
            </div>
          }
        </div>
      } @else {
        <div class="empty">
          Noch keine Einträge für diesen Filter. Die visuelle Guide-Snake protokolliert hier
          automatisch, was sie vom Frontend sieht und wie sie antwortet.
        </div>
      }
    </div>
    `,
  styles: [`
    :host { display: block; height: 100%; }
    .vlog { display: flex; flex-direction: column; height: 100%; min-height: 0; }
    .vlog-head {
      display: flex; justify-content: space-between; align-items: center;
      padding: 4px 8px; border-bottom: 1px solid #1a2d4a;
      background: #0d1e34; font-size: 12px; font-weight: 600; color: #d8c8a8;
      flex-wrap: wrap; gap: 4px;
    }
    .filter-bar { display: flex; gap: 3px; }
    .fbtn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 2px 6px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .fbtn.active { color: #7fffd4; border-color: #2a5a7a; background: #0a2030; }
    .fbtn:hover:not(.active) { color: #c8d8f8; }
    .ghost { background: transparent; border: 1px solid #2a4070; color: #7fffd4; padding: 1px 7px; border-radius: 2px; cursor: pointer; font-size: 11px; }
    .vlist { overflow-y: auto; flex: 1; padding: 6px 10px; min-height: 0; }
    .vitem {
      background: #0f1c30; border: 1px solid #1a2d4a; border-left: 3px solid #3a5a8a;
      border-radius: 3px; padding: 5px 7px; margin-bottom: 5px; font-size: 11px; line-height: 1.4;
    }
    .vitem.has-reply { border-left-color: #d8c8a8; }
    .vitem.is-explain { border-left-color: #7fffd4; background: #0d1e2c; }
    .vitem.is-delta { border-left-color: #3a6a9a; background: #0c1828; }
    .vkind { margin-left: 6px; color: #7fffd4; font-size: 10px; font-weight: 600; }
    .vkind.delta { color: #4a7aaa; }
    .export-btn {
      margin-left: auto; background: transparent; border: none; color: #2a4a6a;
      cursor: pointer; font-size: 11px; padding: 0 2px;
    }
    .export-btn:hover { color: #7fffd4; }
    .vtime { color: #6b8ab8; font-family: monospace; display: flex; align-items: center; gap: 4px; }
    .vroute { color: #7fffd4; margin-top: 2px; font-size: 10px; }
    .vsnap { color: #c8d8f8; margin-top: 3px; white-space: pre-wrap; word-break: break-word; max-height: 80px; overflow: hidden; }
    .vsnap.delta-snap { color: #6b8ab8; font-size: 10px; }
    .vreply { margin-top: 5px; padding-top: 5px; border-top: 1px dashed #2a4070; }
    .vreply-label { color: #d8c8a8; font-weight: 600; margin-right: 4px; }
    .vreply-body { color: #c8d8f8; }
    .vcand-toggle { margin-top: 4px; font-size: 10px; color: #6b8ab8; cursor: pointer; user-select: none; }
    .vcand-toggle:hover { color: #a8c7ff; }
    .vcand-list { margin-top: 4px; padding-left: 8px; border-left: 2px solid #1a2d4a; }
    .vcand-item { display: flex; gap: 6px; padding: 2px 0; font-size: 10px; color: #8aa8d8; }
    .vcand-label { color: #4a6a9a; flex-shrink: 0; min-width: 40px; }
    .vcand-bubble { color: #a8c7ff; }
    .empty { color: #6b8ab8; font-size: 11px; padding: 12px; text-align: center; line-height: 1.4; }
  `],
})
export class VisualSnakeLogComponent implements OnInit, OnDestroy {
  private history = inject(ChatHistoryService);
  private cdr = inject(ChangeDetectorRef);

  entries: UiTickEntry[] = [];
  activeFilter: FilterKind = 'all';
  private sub?: Subscription;

  ngOnInit(): void {
    this.sub = this.history.updated$.subscribe(() => {
      this.rebuild();
      this.cdr.markForCheck();
    });
    this.rebuild();
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  refresh(): void {
    this.rebuild();
  }

  setFilter(f: FilterKind): void {
    this.activeFilter = f;
  }

  filteredEntries(): UiTickEntry[] {
    if (this.activeFilter === 'all') return this.entries;
    if (this.activeFilter === 'guide') return this.entries.filter(e => !!e.replyText);
    return this.entries.filter(e => e.kind === this.activeFilter);
  }

  formatTime(ts: number): string {
    if (!ts) return '--:--';
    const ms = ts > 1e12 ? ts : ts * 1000;
    const d = new Date(ms);
    return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  downloadDebugExport(entry: UiTickEntry): void {
    const raw = JSON.stringify({ entry, timestamp: new Date().toISOString() }, null, 2);
    // Redact API keys from export
    const redacted = raw.replace(/sk-[A-Za-z0-9]+/g, 'sk-[REDACTED]');
    const blob = new Blob([redacted], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `vg-debug-${entry.id.slice(0, 8)}-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  private rebuild(): void {
    const msgs = this.history.getMessages('ananta-visual');
    if (!msgs.length) { this.entries = []; return; }
    const ticks: UiTickEntry[] = [];
    let pendingTick: UiTickEntry | null = null;
    for (const m of msgs) {
      if (m.text?.startsWith('[ui-tick]')) {
        if (pendingTick) ticks.push(pendingTick);
        const snapshot = m.text.slice('[ui-tick]'.length).trim();
        const route = snapshot.split(/\s|\|/)[0]?.trim() || '';
        pendingTick = {
          id: m.id, ts: m.ts, text: m.text, kind: 'tick',
          route: route.startsWith('/') ? route : '',
          snapshot,
          preview: snapshot.length > 220 ? snapshot.slice(0, 220) + '…' : snapshot,
        };
      } else if (m.text?.startsWith('[ui-delta]')) {
        if (pendingTick) ticks.push(pendingTick);
        const content = m.text.slice('[ui-delta]'.length).trim();
        pendingTick = {
          id: m.id, ts: m.ts, text: m.text, kind: 'delta',
          route: '',
          snapshot: content,
          preview: content.length > 220 ? content.slice(0, 220) + '…' : content,
        };
      } else if (m.text?.startsWith('[region-explain]')) {
        if (pendingTick) ticks.push(pendingTick);
        const content = m.text.slice('[region-explain]'.length).trim();
        const route = content.split('|')[0]?.trim() || '';
        const elements = content.split('|').slice(1).map((s: string) => s.trim()).filter(Boolean);
        const preview = elements.length ? elements.join('\n') : content;
        pendingTick = {
          id: m.id, ts: m.ts, text: m.text, kind: 'explain',
          route: route.startsWith('/') ? route : '',
          snapshot: content,
          preview: preview.slice(0, 220),
        };
      } else if (m.isAI && pendingTick) {
        pendingTick.replyId = m.id;
        const raw = m.text ?? '';
        if (raw.includes('__CANDIDATES__:')) {
          try {
            const json = raw.slice(raw.indexOf('__CANDIDATES__:') + '__CANDIDATES__:'.length).trim();
            const candidates: GuideCandidate[] = JSON.parse(json);
            if (candidates.length) {
              pendingTick.candidates = candidates;
              pendingTick.replyText = candidates[0].bubble;
            }
          } catch { pendingTick.replyText = raw; }
        } else {
          pendingTick.replyText = raw.includes('__GUIDE__:')
            ? raw.slice(0, raw.indexOf('__GUIDE__:')).trim()
            : raw;
        }
      }
    }
    if (pendingTick) ticks.push(pendingTick);
    this.entries = ticks.slice().reverse();
  }
}
