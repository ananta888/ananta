import {
  AfterViewInit,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  ViewChild,
  inject,
} from '@angular/core';
import { Subscription } from 'rxjs';

import { PairViewSyncService, PeerCursor } from '../services/pair-view-sync.service';
import { ShareSessionService } from '../services/share-session.service';

const SEGMENTS = 14;
const SEGMENT_DISTANCE = 9;
const SPEED = 14;
const COLORS = Object.freeze([
  '#ff6b6b', '#74c0fc', '#ffd43b', '#e599f7',
  '#ff9f43', '#f783ac', '#a9e34b', '#63e6be',
]);

interface Point { x: number; y: number }
interface RemoteSnake {
  readonly id: string;
  label: string;
  readonly color: string;
  target: Point;
  readonly segments: Point[];
}

/**
 * Pair-only remote pointer renderer. Unlike the full AI-Snake overlay it has
 * no Hub, guide, SSE, DOM-capture or raw-WebRTC dependency. Its sole input is
 * authenticated, E2EE-decoded PairView cursor presence.
 */
@Component({
  selector: 'app-pair-remote-snake-overlay',
  standalone: true,
  template: `<canvas #canvas data-testid="pair-remote-snake-overlay" aria-hidden="true"></canvas>`,
  styles: [`
    :host { position: fixed; inset: 0; z-index: 29850; pointer-events: none; display: block; }
    canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  `],
})
export class PairRemoteSnakeOverlayComponent implements AfterViewInit, OnDestroy {
  @ViewChild('canvas') private canvasRef!: ElementRef<HTMLCanvasElement>;

  private readonly sync = inject(PairViewSyncService);
  private readonly share = inject(ShareSessionService);
  private readonly subscriptions = new Subscription();
  private readonly snakes = new Map<string, RemoteSnake>();
  private context: CanvasRenderingContext2D | null = null;
  private animationFrame = 0;

  @HostListener('window:resize')
  onResize(): void { this.resize(); }

  ngAfterViewInit(): void {
    this.context = this.canvasRef.nativeElement.getContext('2d');
    this.resize();
    this.subscriptions.add(this.sync.peerCursors$.subscribe(cursors => this.updateCursors(cursors)));
    this.subscriptions.add(this.share.publicPairRuntimeState$.subscribe(state => {
      if (state !== 'public') this.snakes.clear();
    }));
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.animationFrame);
    this.subscriptions.unsubscribe();
    this.snakes.clear();
  }

  private updateCursors(cursors: ReadonlyMap<string, PeerCursor>): void {
    if (this.share.publicPairRuntimeState$.value !== 'public') return;
    const activeIds = new Set(cursors.keys());
    for (const id of this.snakes.keys()) {
      if (!activeIds.has(id)) this.snakes.delete(id);
    }
    for (const cursor of cursors.values()) {
      const target = this.viewportPoint(cursor);
      const existing = this.snakes.get(cursor.userId);
      if (existing) {
        existing.target = target;
        existing.label = cursor.userLabel;
        continue;
      }
      this.snakes.set(cursor.userId, {
        id: cursor.userId,
        label: cursor.userLabel,
        color: pairCursorColor(cursor.userId),
        target,
        segments: Array.from({ length: SEGMENTS }, (_, index) => ({
          x: target.x - index * SEGMENT_DISTANCE,
          y: target.y,
        })),
      });
    }
    if (this.snakes.size > 0 && this.animationFrame === 0) {
      this.animationFrame = requestAnimationFrame(() => this.frame());
    } else if (this.snakes.size === 0) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = 0;
      const canvas = this.canvasRef.nativeElement;
      this.context?.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  private viewportPoint(peer: PeerCursor): Point {
    const cursor = peer.cursor;
    const width = Math.max(1, window.innerWidth);
    const height = Math.max(1, window.innerHeight);
    if (typeof cursor.nx === 'number' && typeof cursor.ny === 'number') {
      return { x: cursor.nx * width, y: cursor.ny * height };
    }
    return {
      x: typeof cursor.x === 'number' ? cursor.x : ((cursor.column ?? 0) * 7) + 4,
      y: typeof cursor.y === 'number' ? cursor.y : ((cursor.line ?? 0) * 12) + 4,
    };
  }

  private frame(): void {
    if (this.snakes.size === 0) {
      this.animationFrame = 0;
      return;
    }
    this.animationFrame = requestAnimationFrame(() => this.frame());
    const context = this.context;
    if (!context) return;
    const canvas = this.canvasRef.nativeElement;
    context.clearRect(0, 0, canvas.width, canvas.height);
    for (const snake of this.snakes.values()) {
      this.advance(snake);
      this.draw(context, snake);
    }
  }

  private advance(snake: RemoteSnake): void {
    const head = snake.segments[0];
    const dx = snake.target.x - head.x;
    const dy = snake.target.y - head.y;
    const distance = Math.hypot(dx, dy);
    if (distance > 0.5) {
      const step = Math.min(SPEED, distance);
      head.x += dx / distance * step;
      head.y += dy / distance * step;
    }
    for (let index = 1; index < snake.segments.length; index += 1) {
      const previous = snake.segments[index - 1];
      const current = snake.segments[index];
      const sx = current.x - previous.x;
      const sy = current.y - previous.y;
      const segmentDistance = Math.hypot(sx, sy);
      if (segmentDistance <= SEGMENT_DISTANCE) continue;
      const pull = (segmentDistance - SEGMENT_DISTANCE) / segmentDistance;
      current.x -= sx * pull;
      current.y -= sy * pull;
    }
  }

  private draw(context: CanvasRenderingContext2D, snake: RemoteSnake): void {
    context.save();
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.strokeStyle = snake.color;
    context.lineWidth = 7;
    context.beginPath();
    snake.segments.forEach((segment, index) => {
      if (index === 0) context.moveTo(segment.x, segment.y);
      else context.lineTo(segment.x, segment.y);
    });
    context.stroke();
    const head = snake.segments[0];
    context.fillStyle = snake.color;
    context.beginPath();
    context.arc(head.x, head.y, 6, 0, Math.PI * 2);
    context.fill();
    context.font = '11px system-ui, sans-serif';
    context.fillText(snake.label, head.x + 10, head.y - 9);
    context.restore();
  }

  private resize(): void {
    if (!this.canvasRef) return;
    const canvas = this.canvasRef.nativeElement;
    canvas.width = Math.max(1, window.innerWidth);
    canvas.height = Math.max(1, window.innerHeight);
  }
}

export function pairCursorColor(peerId: string): string {
  let hash = 0;
  for (let index = 0; index < peerId.length; index += 1) {
    hash = (Math.imul(hash, 31) + peerId.charCodeAt(index)) >>> 0;
  }
  return COLORS[hash % COLORS.length];
}
