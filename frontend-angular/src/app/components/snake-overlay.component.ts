import {
  Component, ElementRef, ViewChild, AfterViewInit, OnDestroy, HostListener, inject,
} from '@angular/core';
import { distinctUntilChanged, map, Subscription } from 'rxjs';
import { AiSnakeChatService, SnakeParticipant } from '../services/ai-snake-chat.service';
import { SnakeOverlayService } from '../services/snake-overlay.service';
import { ShareSessionService, ShareParticipant } from '../services/share-session.service';
import { WebrtcCursorService } from '../services/webrtc-cursor.service';
import { SnakeGuideService, GuideStep } from '../services/snake-guide.service';
import { UiWaypointService } from '../services/ui-waypoint.service';

// ── Physics ──────────────────────────────────────────────────────────────────
const NUM_SEGS    = 22;
const SEG_DIST    = 13;   // px between segments
const OWN_SPEED   = 7;    // px/frame: own snake → cursor
const PAIR_SPEED  = 7;    // px/frame: pair-dev snake → remote cursor
const AUTO_SPEED  = 2.8;  // px/frame: AI-room snakes (autonomous)
const GUIDE_SPEED = 5.5;  // px/frame: guided tour snake → waypoint
const GOAL_MARGIN = 70;

// ── Color palette for pair-dev peers ─────────────────────────────────────────
const PAIR_COLORS = [
  '#ff6b6b', '#74c0fc', '#ffd43b', '#e599f7',
  '#ff9f43', '#f783ac', '#a9e34b', '#63e6be',
];

function colorForId(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PAIR_COLORS[h % PAIR_COLORS.length];
}

// ── Types ─────────────────────────────────────────────────────────────────────
interface Seg { x: number; y: number; }

interface SnakeState {
  id: string;
  label: string;
  color: string;
  isOwn: boolean;
  isPairDev: boolean;    // true = cursor-following via WebRTC
  isGuide: boolean;      // true = waypoint-guided tour snake
  segs: Seg[];
  tx: number;            // current movement target in viewport px
  ty: number;
  goalX: number;         // autonomous goal (AI-room / fallback)
  goalY: number;
}

interface Bubble { text: string; born: number; }

// ── Component ────────────────────────────────────────────────────────────────
@Component({
  selector: 'app-snake-overlay',
  standalone: true,
  template: `<canvas #cvs></canvas>`,
  styles: [`:host {
    position: fixed; inset: 0; pointer-events: none; z-index: 29999; display: block;
  }
  canvas { display: block; width: 100%; height: 100%; }`],
})
export class SnakeOverlayComponent implements AfterViewInit, OnDestroy {
  @ViewChild('cvs') canvasRef!: ElementRef<HTMLCanvasElement>;

  private chat     = inject(AiSnakeChatService);
  private overlay  = inject(SnakeOverlayService);
  private share    = inject(ShareSessionService);
  private guide    = inject(SnakeGuideService);
  private waypoint = inject(UiWaypointService);
  // Injecting activates the cursor-broadcast loop as a side-effect
  private _cursor  = inject(WebrtcCursorService);

  private ctx!: CanvasRenderingContext2D;
  private snakes: SnakeState[] = [];
  private mouseX = window.innerWidth  / 2;
  private mouseY = window.innerHeight / 2;

  private bubbles: Bubble[] = [];
  private seenMsgIds = new Set<string>();
  private rafId = 0;
  private subs: Subscription[] = [];

  // ── Guided tour state ──────────────────────────────────────────────────────
  private guideSnake: SnakeState | null = null;
  private guideQueue: GuideStep[] = [];
  private guidePendingBubble: string | null = null;
  private guidePendingDelay = 2500;
  private guideBubbleShown = false;
  private guideBubbles: Bubble[] = [];
  private guideStepTimer: ReturnType<typeof setTimeout> | null = null;
  private guideArriveTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  @HostListener('window:resize')
  onResize(): void { this.initCanvas(); }

  @HostListener('window:mousemove', ['$event'])
  onMouseMove(e: MouseEvent): void {
    this.mouseX = e.clientX;
    this.mouseY = e.clientY;
  }

  ngAfterViewInit(): void {
    this.ctx = this.canvasRef.nativeElement.getContext('2d')!;
    this.initCanvas();

    // Own snake (always present)
    this.spawnSnake('__own__', '', '#7fffd4', true, false);

    // Guide snake — persistent orange companion, always visible alongside the own snake
    this.guideSnake = this.spawnSnake('__guide__', '⚙️', '#ff8c00', false, false, true);
    this.parkGuide();

    // Pair-dev participants → cursor-following snakes
    this.subs.push(
      this.share.state$.pipe(
        map(s => s.participants),
        distinctUntilChanged((a, b) => JSON.stringify(a.map(p => p.id)) === JSON.stringify(b.map(p => p.id))),
      ).subscribe(ps => this.syncPairDevParticipants(ps)),
    );

    // AI-room participants → autonomous snakes
    this.subs.push(
      this.chat.participants$.subscribe(ps => this.syncAiParticipants(ps)),
    );

    // Update pair-dev snake targets from remote cursor map
    this.subs.push(
      this.overlay.remoteCursors$.subscribe(map => {
        for (const [id, pos] of map) {
          const s = this.snakes.find(sn => sn.id === id);
          if (s) { s.tx = pos.x; s.ty = pos.y; }
        }
      }),
    );

    // AI message bubbles (strip __GUIDE__ suffix so overlay bubble is clean)
    this.subs.push(
      this.chat.messages$.subscribe(msgs => {
        for (const m of msgs) {
          if (this.seenMsgIds.has(m.id)) continue;
          const isAi = m.sender_id?.startsWith('ai') ||
                       m.sender_id?.includes('snake') ||
                       m.sender_id?.includes('tutor');
          if (!isAi) continue;
          this.seenMsgIds.add(m.id);
          let rawText = m.text || '';
          const gi = rawText.indexOf('\n\n__GUIDE__:');
          if (gi >= 0) rawText = rawText.slice(0, gi);
          const text = rawText.replace(/\n/g, ' ').slice(0, 60) +
                       (rawText.length > 60 ? '…' : '');
          this.bubbles.push({ text, born: performance.now() });
          if (this.bubbles.length > 3) this.bubbles.shift();
        }
      }),
    );

    // Guided tour
    this.subs.push(
      this.guide.play$.subscribe(steps => this.startGuide(steps)),
    );

    this.rafId = requestAnimationFrame(() => this.loop());
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.rafId);
    this.subs.forEach(s => s.unsubscribe());
    this.stopGuideSequence();
    this.snakes = this.snakes.filter(s => s.id !== '__guide__');
    this.guideSnake = null;
  }

  // ── Canvas ──────────────────────────────────────────────────────────────────

  private initCanvas(): void {
    const c = this.canvasRef.nativeElement;
    c.width  = window.innerWidth;
    c.height = window.innerHeight;
  }

  // ── Snake management ────────────────────────────────────────────────────────

  private spawnSnake(id: string, label: string, color: string, isOwn: boolean, isPairDev: boolean, isGuide = false): SnakeState {
    const cx = isOwn
      ? this.mouseX
      : GOAL_MARGIN + Math.random() * (window.innerWidth  - GOAL_MARGIN * 2);
    const cy = isOwn
      ? this.mouseY
      : GOAL_MARGIN + Math.random() * (window.innerHeight - GOAL_MARGIN * 2);

    const segs: Seg[] = [];
    for (let i = 0; i < NUM_SEGS; i++) segs.push({ x: cx - i * SEG_DIST * 0.6, y: cy });

    const s: SnakeState = { id, label, color, isOwn, isPairDev, isGuide, segs, tx: cx, ty: cy, goalX: cx, goalY: cy };
    this.snakes.push(s);
    return s;
  }

  private syncPairDevParticipants(participants: ShareParticipant[]): void {
    const myId = this.share.currentUserId;
    // Add new
    for (const p of participants) {
      if (p.user_id === myId || p.revoked_at) continue;
      if (!this.snakes.find(s => s.id === p.user_id)) {
        const label = p.device_id || p.user_id.slice(0, 8);
        this.spawnSnake(p.user_id, label, colorForId(p.user_id), false, true);
      }
    }
    // Remove departed
    const activeIds = new Set(
      participants.filter(p => !p.revoked_at && p.user_id !== myId).map(p => p.user_id),
    );
    this.snakes = this.snakes.filter(s => s.isOwn || !s.isPairDev || activeIds.has(s.id));
  }

  private syncAiParticipants(_participants: SnakeParticipant[]): void {
    // AI room participants are not rendered as autonomous snakes — only the guide snake
    // (spawned on-demand during __GUIDE__ steps) and the own cursor snake are shown.
    // Pair-dev participant snakes (isPairDev) are unaffected by this.
    this.snakes = this.snakes.filter(s => s.isOwn || s.isPairDev || s.isGuide);
  }

  // ── Physics ─────────────────────────────────────────────────────────────────

  private loop(): void {
    this.rafId = requestAnimationFrame(() => this.loop());
    this.tick();
    this.draw();
  }

  private tick(): void {
    const W = window.innerWidth;
    const H = window.innerHeight;

    for (const snake of this.snakes) {
      if (snake.isOwn) {
        snake.tx = this.mouseX;
        snake.ty = this.mouseY;
      } else if (snake.isPairDev) {
        // Target set externally via remoteCursors$ subscription;
        // fall back to autonomous if no cursor received yet
        const remote = this.overlay.remoteCursors$.value.get(snake.id);
        if (remote) { snake.tx = remote.x; snake.ty = remote.y; }
        else this.stepAutonomousGoal(snake, W, H);
      } else if (!snake.isGuide) {
        // Guide snake goal is set externally — don't randomize
        this.stepAutonomousGoal(snake, W, H);
      }

      // Advance head toward target
      const head = snake.segs[0];
      const dx = snake.tx - head.x;
      const dy = snake.ty - head.y;
      const dist = Math.hypot(dx, dy);
      const speed = snake.isOwn ? OWN_SPEED
        : snake.isPairDev ? PAIR_SPEED
        : snake.isGuide   ? GUIDE_SPEED
        : AUTO_SPEED;
      if (dist > 0.5) {
        const step = Math.min(speed, dist);
        head.x += (dx / dist) * step;
        head.y += (dy / dist) * step;
      }

      // Guide-snake arrival check
      if (snake.isGuide) this.checkGuideArrival();

      // Pull body segments
      for (let i = 1; i < snake.segs.length; i++) {
        const prev = snake.segs[i - 1];
        const cur  = snake.segs[i];
        const sdx = cur.x - prev.x;
        const sdy = cur.y - prev.y;
        const sd = Math.hypot(sdx, sdy);
        if (sd > SEG_DIST) {
          const pull = (sd - SEG_DIST) / sd;
          cur.x -= sdx * pull;
          cur.y -= sdy * pull;
        }
      }
    }
  }

  private checkGuideArrival(): void {
    if (!this.guideSnake || !this.guidePendingBubble || this.guideBubbleShown) return;
    const h = this.guideSnake.segs[0];
    if (Math.hypot(h.x - this.guideSnake.goalX, h.y - this.guideSnake.goalY) < 30) {
      this.onGuideArrived();
    }
  }

  private stepAutonomousGoal(snake: SnakeState, W: number, H: number): void {
    const head = snake.segs[0];
    if (Math.hypot(head.x - snake.goalX, head.y - snake.goalY) < 20) {
      snake.goalX = GOAL_MARGIN + Math.random() * (W - GOAL_MARGIN * 2);
      snake.goalY = GOAL_MARGIN + Math.random() * (H - GOAL_MARGIN * 2);
    }
    snake.tx = snake.goalX;
    snake.ty = snake.goalY;
  }

  // ── Rendering ───────────────────────────────────────────────────────────────

  private draw(): void {
    const { ctx } = this;
    const c = this.canvasRef.nativeElement;
    ctx.clearRect(0, 0, c.width, c.height);

    for (const snake of this.snakes) this.drawSnake(ctx, snake);

    const own = this.snakes.find(s => s.isOwn);
    if (own) this.drawBubbles(ctx, c, own.segs[0]);

    if (this.guideSnake) this.drawGuideBubbles(ctx, c, this.guideSnake.segs[0]);
  }

  private drawSnake(ctx: CanvasRenderingContext2D, snake: SnakeState): void {
    const len = snake.segs.length;
    const [hr, hg, hb] = hexToRgb(snake.color);

    for (let i = len - 1; i >= 0; i--) {
      const seg  = snake.segs[i];
      const t    = 1 - i / (len - 1);   // 1 = head, 0 = tail
      const r    = Math.round(hr * (0.12 + t * 0.88));
      const g    = Math.round(hg * (0.12 + t * 0.88));
      const b    = Math.round(hb * (0.12 + t * 0.88));
      const size = SEG_DIST * (0.42 + t * 0.48);

      ctx.globalAlpha = 0.15 + t * 0.70;
      ctx.fillStyle   = `rgb(${r},${g},${b})`;
      ctx.beginPath();
      ctx.arc(seg.x, seg.y, size, 0, Math.PI * 2);
      ctx.fill();
    }

    // Head glow
    const head = snake.segs[0];
    const headR = SEG_DIST * 0.88;

    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = snake.color;
    ctx.lineWidth   = 1.5;
    ctx.beginPath();
    ctx.arc(head.x, head.y, headR, 0, Math.PI * 2);
    ctx.stroke();

    // Eye in movement direction
    if (snake.segs.length > 1) {
      const neck = snake.segs[1];
      const edx = head.x - neck.x;
      const edy = head.y - neck.y;
      const ed  = Math.hypot(edx, edy) || 1;
      const ex  = head.x + (edx / ed) * headR * 0.55;
      const ey  = head.y + (edy / ed) * headR * 0.55;
      ctx.globalAlpha = 0.9;
      ctx.fillStyle   = snake.isOwn ? '#fff' : snake.color;
      ctx.beginPath();
      ctx.arc(
        Math.max(2, Math.min(window.innerWidth  - 2, ex)),
        Math.max(2, Math.min(window.innerHeight - 2, ey)),
        snake.isOwn ? 2.2 : 1.8,
        0, Math.PI * 2,
      );
      ctx.fill();
    }

    // Name label for remote snakes
    if (!snake.isOwn && snake.label) {
      ctx.globalAlpha  = 0.7;
      ctx.fillStyle    = snake.color;
      ctx.font         = '10px monospace';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(snake.label, head.x, head.y - headR - 3);
    }

    ctx.globalAlpha  = 1;
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  private drawBubbles(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, head: Seg): void {
    const now = performance.now();
    this.bubbles = this.bubbles.filter(b => now - b.born < 4500);
    for (let bi = 0; bi < this.bubbles.length; bi++) {
      const b    = this.bubbles[bi];
      const fade = Math.max(0, 1 - (now - b.born) / 4500);
      ctx.font   = '11px monospace';
      const tw   = ctx.measureText(b.text).width;
      const bw   = tw + 18;
      const bh   = 20;
      const bx   = Math.max(bw / 2 + 6, Math.min(canvas.width - bw / 2 - 6, head.x));
      const by   = Math.max(bh + 6, head.y - 32 - bi * 26);

      ctx.globalAlpha = fade * 0.85;
      ctx.fillStyle   = '#07111f';
      this.rrect(ctx, bx - bw / 2, by - bh, bw, bh, 4);
      ctx.fill();

      ctx.globalAlpha = fade * 0.65;
      ctx.strokeStyle = '#3affaa';
      ctx.lineWidth   = 1;
      ctx.stroke();

      ctx.globalAlpha  = fade;
      ctx.fillStyle    = '#a8e8c8';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(b.text, bx, by - bh / 2, bw - 12);
    }
    ctx.globalAlpha  = 1;
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  // ── Guided tour ─────────────────────────────────────────────────────────────

  private startGuide(steps: GuideStep[]): void {
    this.stopGuideSequence();
    this.guideQueue = [...steps];
    if (!this.guideSnake) {
      this.guideSnake = this.spawnSnake('__guide__', '⚙️', '#ff8c00', false, false, true);
    }
    this.advanceGuide();
  }

  /** Park the guide snake near the assistant button when idle. */
  private parkGuide(): void {
    if (!this.guideSnake) return;
    const px = this.waypoint.resolve('assistant.snake-chat-btn');
    this.guideSnake.goalX = px?.x ?? (window.innerWidth  - 90);
    this.guideSnake.goalY = px?.y ?? (window.innerHeight - 90);
    this.guideSnake.tx    = this.guideSnake.goalX;
    this.guideSnake.ty    = this.guideSnake.goalY;
  }

  private advanceGuide(): void {
    if (!this.guideQueue.length) { this.endGuide(); return; }
    const step = this.guideQueue.shift()!;
    const pos = this.waypoint.resolve(step.waypoint);
    if (this.guideSnake) {
      const tx = pos?.x ?? (GOAL_MARGIN + Math.random() * (window.innerWidth  - GOAL_MARGIN * 2));
      const ty = pos?.y ?? (GOAL_MARGIN + Math.random() * (window.innerHeight - GOAL_MARGIN * 2));
      this.guideSnake.goalX = tx;
      this.guideSnake.goalY = ty;
      this.guideSnake.tx    = tx;
      this.guideSnake.ty    = ty;
    }
    this.guidePendingBubble = step.bubble;
    this.guidePendingDelay  = step.delay_ms ?? 2500;
    this.guideBubbleShown   = false;
    // Fallback: show bubble after 4s even if snake didn't arrive yet
    if (this.guideArriveTimer) clearTimeout(this.guideArriveTimer);
    this.guideArriveTimer = setTimeout(() => {
      if (!this.guideBubbleShown) this.onGuideArrived();
    }, 4000);
  }

  private onGuideArrived(): void {
    if (this.guideBubbleShown || !this.guidePendingBubble) return;
    this.guideBubbleShown = true;
    if (this.guideArriveTimer) { clearTimeout(this.guideArriveTimer); this.guideArriveTimer = null; }
    this.guideBubbles.push({ text: this.guidePendingBubble, born: performance.now() });
    if (this.guideBubbles.length > 2) this.guideBubbles.shift();
    if (this.guideStepTimer) clearTimeout(this.guideStepTimer);
    this.guideStepTimer = setTimeout(() => {
      this.guidePendingBubble = null;
      this.guideBubbleShown   = false;
      this.advanceGuide();
    }, this.guidePendingDelay);
  }

  /** Stop the current guide sequence but keep the snake visible — park it near the assistant button. */
  private endGuide(): void {
    this.stopGuideSequence();
    this.parkGuide();
  }

  private stopGuideSequence(): void {
    if (this.guideStepTimer)  { clearTimeout(this.guideStepTimer);  this.guideStepTimer  = null; }
    if (this.guideArriveTimer){ clearTimeout(this.guideArriveTimer); this.guideArriveTimer = null; }
    this.guideQueue         = [];
    this.guideBubbles       = [];
    this.guidePendingBubble = null;
    this.guideBubbleShown   = false;
  }

  private drawGuideBubbles(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, head: Seg): void {
    const now = performance.now();
    this.guideBubbles = this.guideBubbles.filter(b => now - b.born < 5000);
    for (let bi = 0; bi < this.guideBubbles.length; bi++) {
      const b    = this.guideBubbles[bi];
      const fade = Math.max(0, 1 - (now - b.born) / 5000);
      ctx.font   = '12px monospace';
      const tw   = ctx.measureText(b.text).width;
      const bw   = tw + 22;
      const bh   = 24;
      const bx   = Math.max(bw / 2 + 6, Math.min(canvas.width - bw / 2 - 6, head.x));
      const by   = Math.max(bh + 6, head.y - 36 - bi * 30);

      // Gold bubble
      ctx.globalAlpha = fade * 0.92;
      ctx.fillStyle   = '#1a1200';
      this.rrect(ctx, bx - bw / 2, by - bh, bw, bh, 5);
      ctx.fill();

      ctx.globalAlpha = fade * 0.9;
      ctx.strokeStyle = '#ffd700';
      ctx.lineWidth   = 1.5;
      ctx.stroke();

      // Pointer triangle down to snake head
      ctx.globalAlpha = fade * 0.9;
      ctx.fillStyle   = '#ffd700';
      ctx.beginPath();
      ctx.moveTo(bx - 5, by);
      ctx.lineTo(bx + 5, by);
      ctx.lineTo(bx, by + 7);
      ctx.closePath();
      ctx.fill();

      ctx.globalAlpha  = fade;
      ctx.fillStyle    = '#ffe066';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(b.text, bx, by - bh / 2, bw - 16);
    }
    ctx.globalAlpha  = 1;
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  private rrect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
}

function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [127, 255, 212];
}
