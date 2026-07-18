import { DOCUMENT } from '@angular/common';
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  HostBinding,
  HostListener,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  VpAssistantOutcomePresentation,
  VpAssistantUiStatus,
  vpAssistantOutcomePresentation,
} from './vp-assistant-bridge.service';
import { CanvasHitTarget, VpAssistantLocation, VpEditorContextEnvelope, VpHelpResponse } from './vp-editor-context.models';

let assistantBubbleSequence = 0;

export type VpAssistantAnchorSide = 'top' | 'right' | 'bottom' | 'left';

export interface VpAssistantRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
}

export interface VpAssistantPlacement {
  side: VpAssistantAnchorSide;
  left: number;
  top: number;
  overlapArea: number;
}

function intersectionArea(left: VpAssistantRect, right: VpAssistantRect): number {
  return Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left))
    * Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top));
}

function positionedRect(left: number, top: number, width: number, height: number): VpAssistantRect {
  return { left, top, right: left + width, bottom: top + height, width, height };
}

/** Pure placement policy: evaluate all four sides and minimise overlap with workflow nodes. */
export function chooseVpAssistantPlacement(options: {
  container: VpAssistantRect;
  target: VpAssistantRect;
  obstacles: readonly VpAssistantRect[];
  bubbleWidth: number;
  bubbleHeight: number;
  padding?: number;
  gap?: number;
}): VpAssistantPlacement {
  const padding = options.padding ?? 8;
  const gap = options.gap ?? 12;
  const width = Math.min(options.bubbleWidth, Math.max(1, options.container.width - padding * 2));
  const height = Math.min(options.bubbleHeight, Math.max(1, options.container.height - padding * 2));
  const centerX = options.target.left + options.target.width / 2;
  const centerY = options.target.top + options.target.height / 2;
  const candidates: Array<{ side: VpAssistantAnchorSide; left: number; top: number }> = [
    { side: 'right', left: options.target.right + gap, top: centerY - height / 2 },
    { side: 'bottom', left: centerX - width / 2, top: options.target.bottom + gap },
    { side: 'left', left: options.target.left - gap - width, top: centerY - height / 2 },
    { side: 'top', left: centerX - width / 2, top: options.target.top - gap - height },
  ];
  const minLeft = options.container.left + padding;
  const maxLeft = Math.max(minLeft, options.container.right - padding - width);
  const minTop = options.container.top + padding;
  const maxTop = Math.max(minTop, options.container.bottom - padding - height);

  return candidates.map((candidate, preference) => {
    const left = Math.min(maxLeft, Math.max(minLeft, candidate.left));
    const top = Math.min(maxTop, Math.max(minTop, candidate.top));
    const rect = positionedRect(left, top, width, height);
    const overlapArea = options.obstacles.reduce((sum, obstacle) => sum + intersectionArea(rect, obstacle), 0);
    const clampDistance = Math.abs(left - candidate.left) + Math.abs(top - candidate.top);
    return { ...candidate, left, top, overlapArea, score: overlapArea + clampDistance, preference };
  }).sort((left, right) => left.score - right.score || left.preference - right.preference)[0];
}

@Component({
  selector: 'app-vp-assistant-bubble',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown.escape)': 'escape()', '(keydown.tab)': 'trapFocus($event)' },
  template: `
    @if (visible && response) {
      <section #bubble class="vp-assistant-bubble" [class.compact]="mode === 'compact'" [class.pinned]="mode === 'pinned'"
               [attr.role]="mode === 'compact' ? 'status' : 'dialog'" aria-live="polite"
               [attr.aria-modal]="false" [attr.aria-label]="'AI-Snake Hilfe für ' + locationLabel(response.location)">
        <header [class.drag-handle]="mode === 'pinned'" (pointerdown)="startDrag($event)">
          <span aria-hidden="true">🐍</span><strong>{{ locationLabel(response.location) }}</strong>
          <span class="spacer"></span>
          @if (mode === 'compact') {
            <button type="button" aria-label="Hilfe erweitern" (click)="modeChange.emit('expanded')">Mehr</button>
          } @else {
            <button type="button" [attr.aria-pressed]="mode === 'pinned'" aria-label="Hilfe anheften"
                    (click)="modeChange.emit(mode === 'pinned' ? 'expanded' : 'pinned')">📌</button>
            <button type="button" aria-label="Hilfe einklappen" (click)="modeChange.emit('compact')">−</button>
          }
          <button type="button" aria-label="Hilfe schließen" (click)="requestClose()">×</button>
        </header>
        <p class="summary">{{ response.summary }}</p>
        @if (mode !== 'compact') {
          <div class="details">
            <p>{{ response.explanation }}</p>
            @if (response.options.length) {
              <h4>Konfiguration</h4><ul>@for (option of response.options; track $index) { <li>{{ optionLabel(option) }}</li> }</ul>
            }
            @if (response.warnings.length) {
              <h4>Hinweise</h4><ul class="warnings">@for (warning of response.warnings; track warning) { <li>{{ warning }}</li> }</ul>
            }
            @if (response.next_actions.length) {
              <h4>Nächste Schritte</h4><ol>@for (action of response.next_actions; track action) { <li>{{ action }}</li> }</ol>
            }
            @if (response.evidence.length) {
              <h4>Belege</h4>
              <ul class="evidence">
                @for (item of response.evidence; track item.evidence_id) {
                  <li>
                    <code>{{ item.source_id || item.evidence_id }}</code>
                    @if (item.path) { <span> · {{ item.path }}@if (item.line_start) {<span>:{{ item.line_start }}</span>}</span> }
                    <span> · {{ item.verification_status }}@if (item.trust_level) { / {{ item.trust_level }}}</span>
                  </li>
                }
              </ul>
            }
            @if (response.context_id) {
              <details><summary>Kontext</summary><code>{{ response.context_id }}</code><div>{{ target?.kind }} · {{ target?.entityId }}</div>
                @if (context) {
                  <div>Repository: {{ context.repository_revision }}</div>
                  <div>CodeCompass: {{ context.codecompass_manifest_hash }}</div>
                  <div>Allowlist: {{ context.source_allowlist_version }}</div>
                  <div>Prompt: {{ context.prompt_version }}</div>
                }
              </details>
            }
            @if (requestStatus !== 'idle') {
              <div class="request-status" [attr.data-outcome]="outcomePresentation().state"
                   [class.request-error]="outcomePresentation().state !== 'current'">
                <strong>{{ outcomePresentation().label }}</strong>
                @if (outcomePresentation().detail) { <span>{{ outcomePresentation().detail }}</span> }
                @if (canCancel) { <button type="button" (click)="cancelRequested.emit()">Abbrechen</button> }
                @if (canRetry) { <button type="button" (click)="retryRequested.emit()">Erneut versuchen</button> }
              </div>
            }
            @if (contextSwitchPending) {
              <div class="context-confirm" role="alert">
                <strong>Der ausgewählte Editor-Kontext hat sich geändert.</strong>
                <p>Die bestehende Unterhaltung nur nach ausdrücklicher Bestätigung auf den neuen Kontext umstellen.</p>
                <div>
                  <button type="button" (click)="contextSwitchConfirmed.emit()">Kontext wechseln und senden</button>
                  <button type="button" (click)="contextSwitchRejected.emit()">Nicht wechseln</button>
                </div>
              </div>
            }
            @if (response.workflow_patch) {
              <div class="patch-offer">
                @if (patchAllowed) {
                  <span>Die Antwort enthält einen Hub-prüfbaren Workflow-Patch.</span>
                  <button type="button" [disabled]="awaitingReply" (click)="patchPreviewRequested.emit()">Sichere Vorschau öffnen</button>
                } @else {
                  <span>Im Nur-Lese-Modus wird der Patch ausschließlich erklärt; es gibt keine Übernahmeaktion.</span>
                }
              </div>
            }
            <form (submit)="submit($event)">
              <label [for]="questionInputId">Frage zu diesem Kontext</label>
              <div class="question-row">
                <input [id]="questionInputId" [(ngModel)]="question" [name]="questionInputId" autocomplete="off" />
                <button type="submit" [disabled]="!question.trim() || awaitingReply || contextSwitchPending">Fragen</button>
              </div>
            </form>
          </div>
        }
      </section>
    }
  `,
  styles: [`
    :host{position:absolute;z-index:20;pointer-events:none}.vp-assistant-bubble{pointer-events:auto;width:min(clamp(360px,42vw,640px),calc(100vw - 16px));max-height:min(70vh,620px);overflow:auto;border:1px solid #e4c54b;border-radius:12px;background:#111a27;color:#eef5ff;box-shadow:0 10px 35px #0008;padding:10px}.vp-assistant-bubble.compact{width:min(360px,calc(100vw - 16px));max-height:150px}.vp-assistant-bubble.pinned{border-color:#68b5ff}header{display:flex;align-items:center;gap:7px;position:sticky;top:-10px;background:#111a27;padding:4px 0;z-index:1}.drag-handle{cursor:grab}.drag-handle:active{cursor:grabbing}.spacer{flex:1}button{border:1px solid #385271;border-radius:5px;background:#172942;color:inherit;padding:4px 8px;cursor:pointer}button:focus-visible,input:focus-visible{outline:2px solid #ffd166;outline-offset:2px}.summary{margin:7px 0}.details{font-size:13px}h4{margin:9px 0 3px}ul,ol{margin:3px 0;padding-left:21px}.warnings{color:#ffd29c}.evidence{font-size:11px}.request-status,.context-confirm,.patch-offer{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:9px 0;padding:7px;border:1px solid #385271;border-radius:6px;background:#0b1624}.request-error{border-color:#d17a63;color:#ffd3c8}.context-confirm{display:block;border-color:#e4c54b}.context-confirm p{margin:4px 0}.context-confirm div{display:flex;gap:6px;flex-wrap:wrap}.patch-offer{border-color:#68b5ff;justify-content:space-between}details code{display:block;overflow-wrap:anywhere;font-size:10px;opacity:.8}.question-row{display:flex;gap:6px}.question-row input{flex:1;min-width:0;background:#07111f;color:inherit;border:1px solid #385271;border-radius:5px;padding:7px}@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}@media(max-width:600px){.vp-assistant-bubble{max-height:55vh}}
  `],
})
export class VpAssistantBubbleComponent implements AfterViewInit, OnChanges, OnDestroy {
  private readonly document = inject(DOCUMENT);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private originFocus: HTMLElement | null = null;
  private repositionTimer: ReturnType<typeof setTimeout> | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private drag: { pointerId: number; clientX: number; clientY: number; left: number; top: number } | null = null;
  private pinnedPosition: { left: number; top: number } | null = null;

  @ViewChild('bubble') bubble?: ElementRef<HTMLElement>;
  @HostBinding('style.left.px') hostLeft = 18;
  @HostBinding('style.top.px') hostTop = 18;
  @HostBinding('attr.data-anchor-side') anchorSide: VpAssistantAnchorSide = 'right';
  readonly questionInputId = `vp-assistant-question-${++assistantBubbleSequence}`;
  @Input() visible = false;
  @Input() mode: 'compact' | 'expanded' | 'pinned' = 'compact';
  @Input() response: VpHelpResponse | null = null;
  @Input() target: CanvasHitTarget | null = null;
  @Input() context: VpEditorContextEnvelope | null = null;
  @Input() requestStatus: VpAssistantUiStatus = 'idle';
  @Input() errorCode: string | null = null;
  @Input() awaitingReply = false;
  @Input() canCancel = false;
  @Input() canRetry = false;
  @Input() contextSwitchPending = false;
  @Input() patchAllowed = true;
  @Output() modeChange = new EventEmitter<'compact' | 'expanded' | 'pinned'>();
  @Output() questionAsked = new EventEmitter<string>();
  @Output() closed = new EventEmitter<void>();
  @Output() cancelRequested = new EventEmitter<void>();
  @Output() retryRequested = new EventEmitter<void>();
  @Output() contextSwitchConfirmed = new EventEmitter<void>();
  @Output() contextSwitchRejected = new EventEmitter<void>();
  @Output() patchPreviewRequested = new EventEmitter<void>();
  question = '';

  ngAfterViewInit(): void {
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => this.scheduleReposition());
      this.resizeObserver.observe(this.host.nativeElement);
    }
    this.scheduleReposition(this.visible && this.mode !== 'compact');
  }

  ngOnChanges(changes: SimpleChanges): void {
    const visibleOpened = changes['visible'] && this.visible && !changes['visible'].previousValue;
    const dialogOpened = changes['mode'] && this.visible && this.mode !== 'compact'
      && changes['mode'].previousValue === 'compact';
    if (visibleOpened || dialogOpened) this.captureOriginFocus(dialogOpened);
    if (changes['mode'] && this.mode !== 'pinned') {
      this.drag = null;
      this.pinnedPosition = null;
    }
    this.scheduleReposition(dialogOpened);
  }

  ngOnDestroy(): void {
    if (this.repositionTimer) clearTimeout(this.repositionTimer);
    this.resizeObserver?.disconnect();
  }

  @HostListener('window:resize')
  onViewportResize(): void { this.scheduleReposition(); }

  @HostListener('document:pointermove', ['$event'])
  dragMove(event: PointerEvent): void {
    if (!this.drag || event.pointerId !== this.drag.pointerId || this.mode !== 'pinned') return;
    const container = this.containerElement();
    const bubble = this.bubble?.nativeElement;
    if (!container || !bubble) return;
    const bounds = this.rect(container.getBoundingClientRect());
    const width = bubble.getBoundingClientRect().width || bubble.offsetWidth || 430;
    const height = bubble.getBoundingClientRect().height || bubble.offsetHeight || 320;
    const maxLeft = Math.max(8, bounds.width - width - 8);
    const maxTop = Math.max(8, bounds.height - height - 8);
    this.hostLeft = Math.min(maxLeft, Math.max(8, this.drag.left + event.clientX - this.drag.clientX));
    this.hostTop = Math.min(maxTop, Math.max(8, this.drag.top + event.clientY - this.drag.clientY));
    this.pinnedPosition = { left: this.hostLeft, top: this.hostTop };
    this.changeDetector.markForCheck();
  }

  @HostListener('document:pointerup', ['$event'])
  @HostListener('document:pointercancel', ['$event'])
  dragEnd(event: PointerEvent): void {
    if (this.drag?.pointerId === event.pointerId) this.drag = null;
  }

  startDrag(event: PointerEvent): void {
    if (this.mode !== 'pinned' || event.button !== 0) return;
    event.preventDefault();
    this.drag = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      left: this.hostLeft,
      top: this.hostTop,
    };
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  }

  reposition(): void {
    if (!this.visible || !this.response || (this.mode === 'pinned' && this.pinnedPosition)) return;
    const container = this.containerElement();
    const bubble = this.bubble?.nativeElement;
    if (!container || !bubble) return;
    const containerRect = this.rect(container.getBoundingClientRect(), container.clientWidth, container.clientHeight);
    const targetRect = this.targetElement(container)?.getBoundingClientRect();
    const anchor = targetRect && targetRect.width > 0 && targetRect.height > 0
      ? this.rect(targetRect)
      : positionedRect(
        containerRect.left + containerRect.width / 2 - 1,
        containerRect.top + containerRect.height / 2 - 1,
        2,
        2,
      );
    const rendered = bubble.getBoundingClientRect();
    const width = rendered.width || bubble.offsetWidth || (this.mode === 'compact' ? 360 : 480);
    const height = rendered.height || bubble.offsetHeight || (this.mode === 'compact' ? 140 : 420);
    const obstacles = Array.from(container.querySelectorAll('[data-semantic-kind="node"]'))
      .map(element => element.getBoundingClientRect())
      .filter(rect => rect.width > 0 && rect.height > 0)
      .map(rect => this.rect(rect));
    const placement = chooseVpAssistantPlacement({
      container: containerRect,
      target: anchor,
      obstacles,
      bubbleWidth: width,
      bubbleHeight: height,
    });
    this.hostLeft = placement.left - containerRect.left + container.scrollLeft;
    this.hostTop = placement.top - containerRect.top + container.scrollTop;
    this.anchorSide = placement.side;
    if (this.mode === 'pinned') this.pinnedPosition = { left: this.hostLeft, top: this.hostTop };
    this.changeDetector.markForCheck();
  }

  trapFocus(event: KeyboardEvent): void {
    if (!this.visible || this.mode === 'compact') return;
    const focusable = this.focusableElements();
    if (!focusable.length) return;
    const active = this.document.activeElement;
    const first = focusable[0];
    const last = focusable.at(-1)!;
    if (event.shiftKey && (active === first || !this.bubble?.nativeElement.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !this.bubble?.nativeElement.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  requestClose(): void {
    this.closed.emit();
    this.returnFocus();
  }

  submit(event: Event): void {
    event.preventDefault();
    const value = this.question.trim();
    if (!value) return;
    this.questionAsked.emit(value);
    this.question = '';
  }

  escape(): void {
    if (this.mode === 'pinned') this.modeChange.emit('expanded');
    else if (this.mode === 'expanded') {
      this.modeChange.emit('compact');
      this.returnFocus();
    } else this.requestClose();
  }

  locationLabel(location: string | VpAssistantLocation): string {
    if (typeof location === 'string') return location;
    const detail = location.field_path || location.entity_id || location.graph_id;
    return `${location.target_kind}: ${detail}`;
  }

  optionLabel(option: string | Record<string, unknown>): string {
    if (typeof option === 'string') return option;
    for (const key of ['label', 'title', 'name', 'description', 'value']) {
      if (typeof option[key] === 'string' && String(option[key]).trim()) return String(option[key]);
    }
    return JSON.stringify(option);
  }

  statusLabel(status: VpAssistantUiStatus): string {
    return vpAssistantOutcomePresentation(status, this.errorCode).label;
  }

  outcomePresentation(): VpAssistantOutcomePresentation {
    return vpAssistantOutcomePresentation(this.requestStatus, this.errorCode);
  }

  private scheduleReposition(focus = false): void {
    if (this.repositionTimer) clearTimeout(this.repositionTimer);
    this.repositionTimer = setTimeout(() => {
      this.repositionTimer = null;
      this.reposition();
      if (focus && this.mode !== 'compact') this.focusableElements()[0]?.focus();
    });
  }

  private captureOriginFocus(force = false): void {
    const active = this.document.activeElement;
    if (!(active instanceof HTMLElement) || this.bubble?.nativeElement.contains(active)) return;
    if (force || !this.originFocus || !this.originFocus.isConnected) this.originFocus = active;
  }

  private returnFocus(): void {
    const origin = this.originFocus;
    this.originFocus = null;
    queueMicrotask(() => {
      if (origin?.isConnected) origin.focus();
    });
  }

  private focusableElements(): HTMLElement[] {
    const root = this.bubble?.nativeElement;
    if (!root) return [];
    return Array.from(root.querySelectorAll<HTMLElement>(
      'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])',
    )).filter(element => element.getAttribute('aria-hidden') !== 'true');
  }

  private containerElement(): HTMLElement | null {
    return this.host.nativeElement.closest<HTMLElement>('.vpe-main') ?? this.host.nativeElement.parentElement;
  }

  private targetElement(container: HTMLElement): Element | null {
    const target = this.target;
    if (!target) return null;
    return Array.from(container.querySelectorAll('[data-semantic-kind][data-entity-id]')).find(element =>
      element.getAttribute('data-semantic-kind') === target.kind
      && element.getAttribute('data-entity-id') === target.entityId,
    ) ?? null;
  }

  private rect(value: DOMRect | ClientRect, fallbackWidth = 0, fallbackHeight = 0): VpAssistantRect {
    const width = value.width || fallbackWidth;
    const height = value.height || fallbackHeight;
    return {
      left: value.left,
      top: value.top,
      right: value.left + width,
      bottom: value.top + height,
      width,
      height,
    };
  }
}
