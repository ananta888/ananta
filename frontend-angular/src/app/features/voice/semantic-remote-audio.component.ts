import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Input,
  OnDestroy,
  ViewChild,
  inject,
} from '@angular/core';
import { Subscription } from 'rxjs';

import { LivekitSfuTransportService } from '../../services/livekit-sfu-transport.service';
import { WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';

interface PendingAudio {
  readonly track: MediaStreamTrack;
  readonly stream: MediaStream | null;
  readonly source: 'ordinary' | 'sfu';
}

@Component({
  selector: 'app-semantic-remote-audio',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="remote-audio-title">
      <h3 id="remote-audio-title">Remote-Audio</h3>
      <audio #audio autoplay controls playsinline></audio>
      <p role="status">{{ status }}</p>
      @if (playbackBlocked) {
        <button type="button" (click)="resume()">Audio wiedergeben</button>
      }
    </section>
  `,
  styles: [`
    :host { display:block; margin-top:1rem; } section { border:1px solid currentColor; border-radius:.6rem; padding:.8rem; }
    audio { width:100%; } button { min-height:2.5rem; }
  `],
})
export class SemanticRemoteAudioComponent implements AfterViewInit, OnDestroy {
  private readonly ordinary = inject(WebrtcMediaSessionService);
  private readonly sfu = inject(LivekitSfuTransportService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private readonly subscriptions = new Subscription();
  private pending: PendingAudio | null = null;
  status = 'Kein Remote-Audio empfangen.';
  playbackBlocked = false;

  @Input() includeSfu = true;

  @ViewChild('audio', { static: true }) private audio!: ElementRef<HTMLAudioElement>;

  constructor() {
    this.subscriptions.add(this.ordinary.remoteTracks$.subscribe(values => {
      const value = [...values].reverse().find(candidate =>
        candidate.track.kind === 'audio' && candidate.track.readyState !== 'ended');
      if (value) {
        this.attach({ track: value.track, stream: value.streams[0] ?? null, source: 'ordinary' });
      } else if (this.pending?.source === 'ordinary') {
        this.detachPlayback();
      }
    }));
    this.subscriptions.add(this.sfu.remoteTrack$.subscribe(value => {
      if (this.includeSfu && value.track.kind === 'audio') {
        this.attach({ track: value.track, stream: null, source: 'sfu' });
      }
    }));
  }

  ngAfterViewInit(): void { if (this.pending) this.apply(this.pending); }

  resume(): void {
    void this.audio.nativeElement.play().then(() => {
      this.playbackBlocked = false;
      this.changeDetector.markForCheck();
    }).catch(() => {
      this.playbackBlocked = true;
      this.changeDetector.markForCheck();
    });
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
    this.detachPlayback();
  }

  private attach(value: PendingAudio): void {
    this.pending = value;
    if (this.audio) this.apply(value);
  }

  private apply(value: PendingAudio): void {
    const stream = value.stream ?? new MediaStream([value.track]);
    this.audio.nativeElement.srcObject = stream;
    this.status = value.source === 'sfu' ? 'SFU-Audio empfangen.' : 'Ordinary Audio empfangen.';
    this.playbackBlocked = false;
    void this.audio.nativeElement.play().catch(() => {
      this.playbackBlocked = true;
      this.changeDetector.markForCheck();
    });
    this.changeDetector.markForCheck();
  }

  private detachPlayback(): void {
    this.pending = null;
    if (this.audio?.nativeElement) this.audio.nativeElement.srcObject = null;
    this.status = 'Kein Remote-Audio empfangen.';
    this.playbackBlocked = false;
    this.changeDetector.markForCheck();
  }
}
