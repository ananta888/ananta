/**
 * PredictionGateService — decides when to send a UI tick to the backend.
 *
 * Applies three sequential gates:
 *   1. Master toggle  (predictive_guide_enabled)
 *   2. TTL            (don't re-fire within predictive_guide_ttl_seconds)
 *   3. Dwell + intent score (predictive_guide_dwell_ms + min_confidence)
 *
 * The 6 intent signals and their maximum weights:
 *   dwell      0.40  — how long the snapshot has been stable
 *   focus      0.25  — a form input has focus
 *   listChange 0.20  — list:N count changed vs previous snapshot
 *   navTab     0.15  — active nav tab changed
 *   contentTab 0.15  — active content tab changed
 *   error      0.30  — err: section present in snapshot
 *
 * Confidence levels:
 *   CONFIRMED  >= min_confidence  (default 0.55 balanced)
 *   LIKELY     >= 0.35
 *   WEAK       >= 0.15
 *   NONE       < 0.15
 */
import { Injectable, inject } from '@angular/core';
import { ChatSessionsService } from './chat-sessions.service';

export type ConfidenceLevel = 'CONFIRMED' | 'LIKELY' | 'WEAK' | 'NONE';

export interface IntentSignals {
  dwell: number;
  focus: number;
  listChange: number;
  navTab: number;
  contentTab: number;
  error: number;
}

export interface IntentScore {
  total: number;
  level: ConfidenceLevel;
  signals: IntentSignals;
}

export interface PugSettings {
  enabled: boolean;
  dwellMs: number;
  minConfidence: number;
  ttlSeconds: number;
  multiCandidates: number;
}

const DEFAULTS: PugSettings = {
  enabled: false,
  dwellMs: 1500,
  minConfidence: 0.55,
  ttlSeconds: 20,
  multiCandidates: 3,
};

@Injectable({ providedIn: 'root' })
export class PredictionGateService {
  private sessions = inject(ChatSessionsService);

  private dwellSince = 0;
  private dwellSnapshot = '';
  private lastSentAt = 0;
  private prevSnapshot = '';

  /** Read PUG settings from the ananta-visual session (falls back to defaults). */
  getSettings(): PugSettings {
    const sess = this.sessions.sessions$.value.find(s => s.id === 'ananta-visual');
    const s = sess?.settings ?? {};
    return {
      enabled:        Boolean(s['predictive_guide_enabled']       ?? DEFAULTS.enabled),
      dwellMs:        Number(s['predictive_guide_dwell_ms']       ?? DEFAULTS.dwellMs),
      minConfidence:  Number(s['predictive_guide_min_confidence'] ?? DEFAULTS.minConfidence),
      ttlSeconds:     Number(s['predictive_guide_ttl_seconds']    ?? DEFAULTS.ttlSeconds),
      multiCandidates:Number(s['predictive_guide_multi_candidates']?? DEFAULTS.multiCandidates),
    };
  }

  /**
   * Must be called immediately when a new snapshot is observed — BEFORE
   * scheduling the dwell timer — so the dwell clock starts running in
   * real time. If the snapshot changes again before the timer fires, the
   * clock resets (correct: dwell means "stable for N ms").
   */
  notifyChange(snapshot: string, nowMs = Date.now()): void {
    if (snapshot !== this.dwellSnapshot) {
      this.dwellSince = nowMs;
      this.dwellSnapshot = snapshot;
    }
  }

  /**
   * Evaluate whether a tick should be sent for the given snapshot.
   *
   * Call AFTER notifyChange() so the dwell clock is already running.
   * Returns null when any gate blocks (disabled, TTL, dwell not elapsed).
   * Returns IntentScore otherwise; caller fires tick when level is CONFIRMED or LIKELY.
   */
  evaluate(snapshot: string, nowMs = Date.now()): IntentScore | null {
    const cfg = this.getSettings();
    if (!cfg.enabled) return null;

    // TTL gate
    if (nowMs - this.lastSentAt < cfg.ttlSeconds * 1000) return null;

    // Dwell gate: use elapsed since notifyChange() set the clock
    const elapsed = this.dwellSnapshot === snapshot ? (nowMs - this.dwellSince) : 0;
    if (elapsed < cfg.dwellMs) return null;

    return this.score(snapshot, this.prevSnapshot, elapsed, cfg.dwellMs, cfg.minConfidence);
  }

  /** Call after a tick is sent to reset the TTL clock and record prev snapshot. */
  markSent(snapshot: string, nowMs = Date.now()): void {
    this.lastSentAt = nowMs;
    this.prevSnapshot = snapshot;
  }

  private score(
    curr: string, prev: string,
    elapsed: number, dwellMs: number,
    minConfidence: number,
  ): IntentScore {
    const dwell      = Math.min(elapsed / dwellMs, 1.0) * 0.40;
    const focus      = curr.includes('focus:input') ? 0.25 : 0;
    const listChange = this.listCount(curr) !== this.listCount(prev) ? 0.20 : 0;
    const navTab     = this.activeNav(curr) !== this.activeNav(prev) ? 0.15 : 0;
    const contentTab = this.activeTab(curr) !== this.activeTab(prev) ? 0.15 : 0;
    const error      = / err:|^err:/.test(curr) ? 0.30 : 0;

    const total = Math.min(dwell + focus + listChange + navTab + contentTab + error, 1.0);
    const level = this.toLevel(total, minConfidence);
    return { total, level, signals: { dwell, focus, listChange, navTab, contentTab, error } };
  }

  private toLevel(score: number, minConfidence: number): ConfidenceLevel {
    if (score >= minConfidence) return 'CONFIRMED';
    if (score >= 0.35) return 'LIKELY';
    if (score >= 0.15) return 'WEAK';
    return 'NONE';
  }

  private listCount(snap: string): string {
    return snap.match(/\blist:(\d+)/)?.[1] ?? '';
  }

  private activeNav(snap: string): string {
    const seg = snap.match(/\bnav:[^|]*/)?.[0] ?? '';
    return seg.match(/([^|:,]+)\*/)?.[1]?.trim() ?? '';
  }

  private activeTab(snap: string): string {
    const seg = snap.match(/\btab:[^|]*/)?.[0] ?? '';
    return seg.match(/([^|:,]+)\*/)?.[1]?.trim() ?? '';
  }
}
