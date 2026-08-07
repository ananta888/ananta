import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { OrdinaryAudioState } from '../../services/webrtc-media-session.service';
import { MediaPublicationView } from '../../services/webrtc-media-publication.service';
import { WebrtcRemoteVideoComponent } from './webrtc-remote-video.component';

@Component({
  selector: 'app-webrtc-media-panel', standalone: true, imports: [WebrtcRemoteVideoComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="ordinary-media-heading">
      <h4 id="ordinary-media-heading">Ordinary Audio/Video</h4>
      <p class="notice">Capture startet ausschließlich nach Ihrer sichtbaren Auswahl.</p>
      <p class="notice" role="note">
        Kamera und Mikrofon benötigen HTTPS oder localhost sowie die Browserfreigabe für diese Website.
      </p>
      @if (!captureEnabled || !videoCaptureEnabled) {
        <p role="note">{{ reasonLabel(captureReason) }} <code>{{ captureReason }}</code></p>
      }
      <article data-source="microphone" [attr.data-status]="audioState.status">
        <strong>Mikrofon</strong>
        <span>{{ statusLabel(audioState.status) }}</span>
        @if (audioState.reasonCode) {
          <span role="status">{{ reasonLabel(audioState.reasonCode) }} <code>{{ audioState.reasonCode }}</code></span>
        }
        <div class="actions">
          <button type="button" [disabled]="!captureEnabled || microphoneBusy()" (click)="startMicrophone.emit()">
            Mikrofon freigeben
          </button>
          @if (microphoneStarted()) {
            <button type="button" (click)="stopMicrophone.emit()">Mikrofon stoppen</button>
          }
          @if (audioState.status === 'active') {
            <button type="button" (click)="muteMicrophone.emit(true)">Mikrofon stummschalten</button>
          } @else if (audioState.status === 'muted') {
            <button type="button" (click)="muteMicrophone.emit(false)">Mikrofon einschalten</button>
          }
        </div>
      </article>
      <div class="actions video-actions">
        <button type="button" [disabled]="!videoCaptureEnabled || sourceBusy('camera')" (click)="startCamera.emit()">
          Kamera freigeben
        </button>
        <button type="button" [disabled]="!videoCaptureEnabled || sourceBusy('screen')" (click)="startScreen.emit()">
          Bildschirm freigeben
        </button>
      </div>
      @for (publication of publications; track publication.publicationId) {
        <article [attr.data-source]="publication.source" [attr.data-status]="publication.status">
          <strong>{{ publication.captureLabel }}</strong>
          <span>{{ statusLabel(publication.status) }}</span>
          @if (publication.reasonCode) {
            <span role="status">{{ reasonLabel(publication.reasonCode) }} <code>{{ publication.reasonCode }}</code></span>
          }
          @if (publication.local && publication.status !== 'ended') {
            <button type="button" (click)="stop.emit(publication.publicationId)">
              {{ publication.captureLabel }} stoppen
            </button>
            @if (publication.status === 'active' || publication.status === 'muted') {
              <button type="button" (click)="replace.emit(publication.publicationId)">
                {{ publication.captureLabel }} wechseln
              </button>
              <button type="button" (click)="mute.emit({ publicationId: publication.publicationId, muted: publication.status === 'active' })">
                {{ publication.status === 'active' ? 'Stummschalten' : 'Einschalten' }}
              </button>
            }
          }
          @if (!publication.local && publication.source === 'remote_video') {
            <app-webrtc-remote-video [publication]="publication" />
          }
        </article>
      }
      @if (!hasRemoteVideo()) {
        <p class="notice" role="status">Noch kein Remote-Video empfangen.</p>
      }
    </section>
  `,
  styles: [`
    :host { display: block; } section { border: 1px solid #3b3d46; border-radius: .6rem; padding: .8rem; }
    .notice { color: #c8cbd2; } .actions, article { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
    .video-actions { margin-top: .75rem; }
    article { margin-top: .5rem; padding: .5rem; background: #25272d; } article strong { flex: 1; }
  `],
})
export class WebrtcMediaPanelComponent {
  @Input() captureEnabled = false;
  @Input() videoCaptureEnabled = false;
  @Input() captureReason = 'ordinary_media_capture_not_authorized';
  @Input() audioState: OrdinaryAudioState = {
    status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null,
  };
  @Input() publications: readonly MediaPublicationView[] = [];
  @Output() readonly startMicrophone = new EventEmitter<void>();
  @Output() readonly stopMicrophone = new EventEmitter<void>();
  @Output() readonly muteMicrophone = new EventEmitter<boolean>();
  @Output() readonly startCamera = new EventEmitter<void>();
  @Output() readonly startScreen = new EventEmitter<void>();
  @Output() readonly stop = new EventEmitter<string>();
  @Output() readonly replace = new EventEmitter<string>();
  @Output() readonly mute = new EventEmitter<Readonly<{ publicationId: string; muted: boolean }>>();

  microphoneStarted(): boolean {
    return ['requesting_permission', 'active', 'muted'].includes(this.audioState.status);
  }

  microphoneBusy(): boolean {
    return this.microphoneStarted();
  }

  sourceBusy(source: 'camera' | 'screen'): boolean {
    return this.publications.some(publication => publication.local && publication.source === source
      && ['requesting_permission', 'active', 'muted'].includes(publication.status));
  }

  statusLabel(status: string): string {
    return ({
      idle: 'bereit', requesting_permission: 'Browserfreigabe wird angefragt', active: 'aktiv',
      muted: 'stumm', ended: 'beendet', failed: 'fehlgeschlagen',
    } as Record<string, string>)[status] ?? 'unbekannt';
  }

  hasRemoteVideo(): boolean {
    return this.publications.some(publication => !publication.local
      && publication.source === 'remote_video' && publication.status !== 'ended');
  }

  reasonLabel(reasonCode: string): string {
    return ({
      ordinary_media_activation_required: 'Audio/Video muss zuerst beim Hub aktiviert werden.',
      ordinary_media_capture_not_authorized: 'Der Hub hat die Medienaufnahme nicht freigegeben.',
      ordinary_media_publication_disabled: 'Ordinary Media ist in diesem Netzwerkprofil deaktiviert.',
      ordinary_media_webrtc_transport_required: 'Die direkte WebRTC-Verbindung ist noch nicht verfügbar.',
      ordinary_media_session_missing: 'Es ist noch keine aktive Pair-Session ausgewählt.',
      ordinary_media_hub_offline: 'Der Hub ist nicht erreichbar; neue Medienfreigaben sind gesperrt.',
      microphone_permission_denied: 'Der Browser hat den Mikrofonzugriff abgelehnt.',
      publication_permission_denied: 'Der Browser hat den Kamera- oder Bildschirmzugriff abgelehnt.',
      browser_media_devices_unavailable: 'Dieses Browserfenster kann nicht auf Mediengeräte zugreifen.',
      browser_display_capture_unavailable: 'Dieser Browser unterstützt keine Bildschirmfreigabe.',
      publication_authorization_expired: 'Die Freigabe des Hubs ist abgelaufen.',
      remote_track_unavailable: 'Der entfernte Video-Track ist nicht mehr verfügbar.',
    } as Record<string, string>)[reasonCode] ?? 'Medienstatus des Hubs oder Browsers:';
  }
}
