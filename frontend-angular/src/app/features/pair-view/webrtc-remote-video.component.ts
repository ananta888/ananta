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

import { MediaPublicationView } from '../../services/webrtc-media-publication.service';
import {
  RemoteVideoRelease,
  WebrtcRemoteVideoRenderService,
} from './webrtc-remote-video-render.service';

type RemoteVideoStatus = 'loading' | 'playing' | 'paused' | 'blocked' | 'unavailable';

@Component({
  selector: 'app-webrtc-remote-video',
  standalone: true,
  providers: [WebrtcRemoteVideoRenderService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <figure [attr.data-status]="status" [attr.data-publication-id]="publication.publicationId">
      <div class="media-frame">
        <video #video autoplay muted playsinline [attr.aria-label]="publication.captureLabel"></video>
        @if (status !== 'playing') {
          <div class="fallback" role="status">
            <strong>{{ publication.captureLabel }}</strong>
            <span>{{ statusLabel }}</span>
          </div>
        }
      </div>
      <figcaption>
        <span>{{ publication.captureLabel }}</span>
        <span>{{ statusLabel }}</span>
        @if (status === 'blocked') {
          <button type="button" (click)="resume()">Video wiedergeben</button>
        }
      </figcaption>
    </figure>
  `,
  styles: [`
    :host { display: block; flex-basis: 100%; }
    figure { margin: .5rem 0 0; }
    .media-frame { position: relative; min-height: 10rem; overflow: hidden; border-radius: .5rem; background: #080f1d; }
    video { display: block; width: 100%; max-height: 25rem; object-fit: contain; background: #080f1d; }
    .fallback { position: absolute; inset: 0; display: grid; place-content: center; gap: .4rem; text-align: center; padding: 1rem; }
    figcaption { display: flex; align-items: center; justify-content: space-between; gap: .75rem; flex-wrap: wrap; margin-top: .4rem; }
    button { min-height: 2.5rem; }
  `],
})
export class WebrtcRemoteVideoComponent implements AfterViewInit, OnChanges, OnDestroy {
  private readonly renderer = inject(WebrtcRemoteVideoRenderService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private release: RemoteVideoRelease | null = null;
  private listeners: Array<() => void> = [];
  private ready = false;

  @Input({ required: true }) publication!: MediaPublicationView;
  @ViewChild('video', { static: true }) private video!: ElementRef<HTMLVideoElement>;

  status: RemoteVideoStatus = 'loading';

  get statusLabel(): string {
    if (this.status === 'playing') return 'Remote-Video wird wiedergegeben';
    if (this.status === 'paused') return 'Remote-Video ist pausiert';
    if (this.status === 'blocked') return 'Browser wartet auf Wiedergabefreigabe';
    if (this.status === 'unavailable') return 'Remote-Video ist nicht verfügbar';
    return 'Remote-Video wird geladen';
  }

  ngAfterViewInit(): void {
    this.ready = true;
    this.attach();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.ready && changes['publication']) this.attach();
  }

  ngOnDestroy(): void { this.detach(); }

  resume(): void { this.requestPlayback(); }

  private attach(): void {
    this.detach();
    if (!this.publication || this.publication.local || this.publication.source !== 'remote_video'
        || this.publication.status === 'ended' || this.publication.status === 'failed') {
      this.setStatus('unavailable');
      return;
    }
    const element = this.video.nativeElement;
    this.listen(element, 'playing', () => this.setStatus('playing'));
    this.listen(element, 'waiting', () => this.setStatus('loading'));
    this.listen(element, 'pause', () => this.setStatus('paused'));
    this.listen(element, 'ended', () => this.setStatus('unavailable'));
    this.listen(element, 'error', () => this.setStatus('unavailable'));
    try {
      this.release = this.renderer.attach(this.publication.publicationId, element);
      this.setStatus(this.publication.status === 'muted' ? 'paused' : 'loading');
      this.requestPlayback();
    } catch {
      this.setStatus('unavailable');
    }
  }

  private requestPlayback(): void {
    try {
      const playback = this.video.nativeElement.play();
      void playback.then(() => this.setStatus('playing')).catch(() => this.setStatus('blocked'));
    } catch {
      this.setStatus('blocked');
    }
  }

  private setStatus(status: RemoteVideoStatus): void {
    this.status = status;
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
