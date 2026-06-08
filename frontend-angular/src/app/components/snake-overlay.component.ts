import {
  Component, ElementRef, ViewChild, AfterViewInit, OnDestroy, HostListener, inject,
} from '@angular/core';
import { Subscription } from 'rxjs';
import { AiSnakeChatService } from '../services/ai-snake-chat.service';

interface Seg { x: number; y: number; }
interface Bubble { text: string; born: number; }

const CELL = 16;
const TICK_MS = 145;
const TRAIL = 24;
const MARGIN = 3;

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

  private chat = inject(AiSnakeChatService);

  private ctx!: CanvasRenderingContext2D;
  private segs: Seg[] = [];
  private dir = { x: 1, y: 0 };
  private cols = 0;
  private rows = 0;
  private rafId = 0;
  private lastTick = 0;
  private bubbles: Bubble[] = [];
  private seenIds = new Set<string>();
  private msgSub?: Subscription;

  @HostListener('window:resize')
  onResize(): void { this.initCanvas(); }

  ngAfterViewInit(): void {
    const canvas = this.canvasRef.nativeElement;
    this.ctx = canvas.getContext('2d')!;
    this.initCanvas();
    this.spawnSnake();
    this.rafId = requestAnimationFrame(ts => this.loop(ts));
    this.msgSub = this.chat.messages$.subscribe(msgs => {
      for (const m of msgs) {
        if (this.seenIds.has(m.id)) continue;
        const isAi = m.sender_id?.startsWith('ai') || m.sender_id?.includes('snake') || m.sender_id?.includes('tutor');
        if (!isAi) continue;
        this.seenIds.add(m.id);
        const text = (m.text || '').replace(/\n/g, ' ').slice(0, 55) + ((m.text || '').length > 55 ? '…' : '');
        this.bubbles.push({ text, born: performance.now() });
        if (this.bubbles.length > 3) this.bubbles.shift();
      }
    });
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.rafId);
    this.msgSub?.unsubscribe();
  }

  private initCanvas(): void {
    const c = this.canvasRef.nativeElement;
    c.width = window.innerWidth;
    c.height = window.innerHeight;
    this.cols = Math.floor(window.innerWidth / CELL);
    this.rows = Math.floor(window.innerHeight / CELL);
  }

  private spawnSnake(): void {
    const cx = Math.floor(this.cols / 2);
    const cy = Math.floor(this.rows / 2);
    this.segs = [];
    for (let i = TRAIL - 1; i >= 0; i--) this.segs.push({ x: cx - i, y: cy });
    this.dir = { x: 1, y: 0 };
  }

  private loop(ts: number): void {
    this.rafId = requestAnimationFrame(t => this.loop(t));
    if (ts - this.lastTick >= TICK_MS) {
      this.lastTick = ts;
      this.tick();
    }
    this.draw(ts);
  }

  private tick(): void {
    const head = this.segs[this.segs.length - 1];
    this.steer(head);
    const nx = ((head.x + this.dir.x) % this.cols + this.cols) % this.cols;
    const ny = ((head.y + this.dir.y) % this.rows + this.rows) % this.rows;
    this.segs.push({ x: nx, y: ny });
    if (this.segs.length > TRAIL) this.segs.shift();
  }

  private steer(head: Seg): void {
    const nearLeft   = head.x <= MARGIN && this.dir.x < 0;
    const nearRight  = head.x >= this.cols - 1 - MARGIN && this.dir.x > 0;
    const nearTop    = head.y <= MARGIN && this.dir.y < 0;
    const nearBottom = head.y >= this.rows - 1 - MARGIN && this.dir.y > 0;

    if (nearLeft || nearRight) {
      this.dir = Math.random() < 0.5 ? { x: 0, y: 1 } : { x: 0, y: -1 };
      return;
    }
    if (nearTop || nearBottom) {
      this.dir = Math.random() < 0.5 ? { x: 1, y: 0 } : { x: -1, y: 0 };
      return;
    }
    // Random wander
    if (Math.random() < 0.06) {
      const perps = this.dir.x !== 0
        ? [{ x: 0, y: 1 }, { x: 0, y: -1 }]
        : [{ x: 1, y: 0 }, { x: -1, y: 0 }];
      this.dir = perps[Math.floor(Math.random() * 2)];
    }
  }

  private draw(ts: number): void {
    const { ctx } = this;
    const c = this.canvasRef.nativeElement;
    ctx.clearRect(0, 0, c.width, c.height);

    const len = this.segs.length;
    for (let i = 0; i < len; i++) {
      const seg = this.segs[i];
      const t = i / (len - 1);
      const isHead = i === len - 1;

      // Gradient: tail dim dark-green → head bright aquamarine
      const r = Math.round(5  + t * 30);
      const g = Math.round(80 + t * 175);
      const b = Math.round(50 + t * 162);
      ctx.globalAlpha = 0.15 + t * 0.72;
      ctx.fillStyle = `rgb(${r},${g},${b})`;

      const pad = isHead ? 1 : 3;
      const size = CELL - pad * 2;
      const rad = isHead ? 5 : 3;
      this.rrect(ctx, seg.x * CELL + pad, seg.y * CELL + pad, size, size, rad);
      ctx.fill();

      // Head: bright outline glow
      if (isHead) {
        ctx.globalAlpha = 0.55;
        ctx.strokeStyle = '#7fffd4';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Eyes
        const ex = seg.x * CELL + CELL / 2 + this.dir.x * 3;
        const ey = seg.y * CELL + CELL / 2 + this.dir.y * 3;
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(ex, ey, 2, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Bubbles
    const now = ts;
    this.bubbles = this.bubbles.filter(b => now - b.born < 4500);
    if (this.bubbles.length > 0 && this.segs.length > 0) {
      const head = this.segs[this.segs.length - 1];
      const hx = head.x * CELL + CELL / 2;
      const hy = head.y * CELL;

      for (let bi = 0; bi < this.bubbles.length; bi++) {
        const b = this.bubbles[bi];
        const age = now - b.born;
        const fade = Math.max(0, 1 - age / 4500);
        const offsetY = -(bi * 28 + 32);

        ctx.font = '11px monospace';
        const tw = ctx.measureText(b.text).width;
        const bw = tw + 18;
        const bh = 20;
        const bx = Math.max(bw / 2 + 4, Math.min(c.width - bw / 2 - 4, hx));
        const by = Math.max(bh + 4, hy + offsetY);

        ctx.globalAlpha = fade * 0.85;
        ctx.fillStyle = '#07111f';
        this.rrect(ctx, bx - bw / 2, by - bh, bw, bh, 4);
        ctx.fill();

        ctx.globalAlpha = fade * 0.7;
        ctx.strokeStyle = '#3affaa';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.globalAlpha = fade;
        ctx.fillStyle = '#a8e8c8';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(b.text, bx, by - bh / 2, bw - 12);
      }
    }

    ctx.globalAlpha = 1;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  private rrect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
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
