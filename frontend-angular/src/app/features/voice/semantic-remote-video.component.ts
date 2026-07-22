import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';

import {
  SfuBroadcastVideoRenderService,
  type SfuRemoteVideoView,
} from '../../services/sfu-broadcast-video-render.service';
import type { SfuRelease } from '../../services/sfu-room-session.ports';

@Component({
  selector: 'app-semantic-remote-video',
  standalone: true,
  templateUrl: './semantic-remote-video.component.html',
  styleUrl: './semantic-remote-video.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SemanticRemoteVideoComponent implements AfterViewInit, OnChanges, OnDestroy {
  private readonly renderer = inject(SfuBroadcastVideoRenderService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private release: SfuRelease | null = null;
  private listeners: Array<() => void> = [];
  private ready = false;

  @Input({ required: true }) view!: SfuRemoteVideoView;
  @ViewChild('video', { static: true }) private video!: ElementRef<HTMLVideoElement>;

  status: 'loading' | 'playing' | 'paused' | 'fallback' = 'loading';

  get statusLabel(): string {
    if (this.view?.state === 'muted' || this.status === 'paused') return 'Pausiert';
    if (this.status === 'playing') return 'Wird wiedergegeben';
    if (this.status === 'fallback') return 'Video nicht verfügbar';
    return 'Video wird geladen';
  }

  ngAfterViewInit(): void {
    this.ready = true;
    this.attach();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.ready && changes['view']) this.attach();
  }

  ngOnDestroy(): void { this.detach(); }

  private attach(): void {
    this.detach();
    if (!this.view) return;
    const element = this.video.nativeElement;
    const setStatus = (status: typeof this.status) => {
      this.status = status;
      this.changeDetector.markForCheck();
    };
    this.listen(element, 'playing', () => setStatus('playing'));
    this.listen(element, 'waiting', () => setStatus('loading'));
    this.listen(element, 'pause', () => setStatus('paused'));
    this.listen(element, 'ended', () => setStatus('fallback'));
    this.listen(element, 'error', () => setStatus('fallback'));
    this.status = this.view.state === 'muted' ? 'paused' : 'loading';
    try {
      this.release = this.renderer.attach(this.view, element);
    } catch {
      this.status = 'fallback';
    }
    this.changeDetector.markForCheck();
  }

  private listen(element: HTMLVideoElement, event: string, callback: () => void): void {
    element.addEventListener(event, callback);
    this.listeners.push(() => element.removeEventListener(event, callback));
  }

  private detach(): void {
    this.release?.();
    this.release = null;
    for (const remove of this.listeners.splice(0).reverse()) remove();
    if (this.video?.nativeElement) this.video.nativeElement.srcObject = null;
  }
}
