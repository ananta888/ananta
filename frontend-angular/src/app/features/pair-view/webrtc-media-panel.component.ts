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
      @if (publicPair && e2eeProtected) {
        <p class="notice" role="status" data-testid="public-media-e2ee-ready">
          Public-Pair-Medien werden vor dem WebRTC-Transport mit sitzungs- und verbindungsgebundenen Schlüsseln verschlüsselt.
        </p>
      } @else if (publicPair) {
        <p class="notice" role="status" data-testid="public-media-e2ee-pending">
          Public-Pair-Capture bleibt gesperrt, bis beide Browser den Medien-E2EE-Pfad bestätigt haben.
        </p>
      }
      @if (!captureEnabled || !videoCaptureEnabled) {
        <p role="note">{{ reasonLabel(captureReason) }} <code>{{ captureReason }}</code></p>
      }
      <p class="notice" role="status" data-testid="ordinary-media-operation-status">
        Letzter Medienstatus: <code>{{ captureReason }}</code>
      </p>
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
  @Input() publicPair = false;
  @Input() e2eeProtected = false;
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
      ordinary_media_activation_required: 'Audio/Video muss zuerst bei der Pair-Autorität aktiviert werden.',
      ordinary_media_capture_not_authorized: 'Die Pair-Autorität hat die Medienaufnahme nicht freigegeben.',
      ordinary_media_publication_disabled: 'Ordinary Media ist in diesem Netzwerkprofil deaktiviert.',
      ordinary_media_webrtc_transport_required: 'Die direkte WebRTC-Verbindung ist noch nicht verfügbar.',
      ordinary_media_session_missing: 'Es ist noch keine aktive Pair-Session ausgewählt.',
      ordinary_media_hub_offline: 'Der Hub ist nicht erreichbar; neue Medienfreigaben sind gesperrt.',
      ordinary_media_public_authority_offline: 'Die Public-Pair-Autorität ist nicht erreichbar.',
      ordinary_media_activation_stale: 'Die Pair-Session hat sich während der Medienaktivierung geändert.',
      public_ordinary_media_e2ee_not_ready: 'Die bestätigte Medienverschlüsselung ist noch nicht bereit.',
      public_ordinary_media_e2ee_awaiting_security: 'Die Pair-Sicherheitsbindung wird noch bestätigt.',
      public_ordinary_media_e2ee_awaiting_peer: 'Das Gegenüber muss Ordinary Audio/Video ebenfalls aktivieren.',
      public_ordinary_media_e2ee_negotiating: 'Beide Browser richten den Medien-E2EE-Pfad ein.',
      public_ordinary_media_e2ee_unavailable: 'Sichere Public-Pair-Medien sind in diesem Browser nicht verfügbar.',
      public_ordinary_media_e2ee_context_mismatch: 'Der Medien-E2EE-Status gehört nicht zur aktiven Pair-Session.',
      media_e2ee_transform_unsupported: 'Dieser Browser unterstützt den benötigten Medien-E2EE-Pfad nicht.',
      media_e2ee_transform_install_failed: 'Der Medien-E2EE-Pfad konnte nicht eingerichtet werden.',
      public_media_runtime_unsupported: 'Dieser Browser stellt den standardisierten Public-Medien-E2EE-Pfad nicht bereit.',
      public_media_contract_not_ready: 'Der separate signierte Public-Medienvertrag ist noch nicht verfügbar.',
      public_media_contract_missing: 'Der separate signierte Public-Medienvertrag fehlt.',
      public_media_contract_expired: 'Der Public-Medienvertrag ist abgelaufen; eine neue Pair-Session ist erforderlich.',
      public_media_transform_not_prepared: 'Die WebRTC-Medientransforms sind noch nicht vorbereitet.',
      public_media_peer_consent_pending: 'Das Gegenüber muss Ordinary Audio/Video ebenfalls aktivieren.',
      public_media_peer_activation_pending: 'Das Gegenüber muss Ordinary Audio/Video ebenfalls aktivieren.',
      public_media_transport_closed: 'Die direkte WebRTC-Verbindung wurde geschlossen; für Public-Medien ist eine neue Pair-Session erforderlich.',
      public_media_fresh_connection_required: 'Die Verbindung muss mit frischen Medienschlüsseln in einer neuen Pair-Session aufgebaut werden.',
      public_media_security_context_changed: 'Die Pair-Sicherheitsbindung hat sich geändert; Medien wurden beendet.',
      public_media_opus_unsupported: 'Dieser Browser bietet den ausgehandelten Opus-Audiocodec nicht an.',
      public_media_vp8_unsupported: 'Dieser Browser bietet den ausgehandelten VP8-Videocodec nicht an.',
      public_media_topology_invalid: 'Der ausgehandelte Medienpfad stimmt nicht mit den freigegebenen Slots überein.',
      media_e2ee_worker_failed: 'Der isolierte Medien-E2EE-Prozess ist fehlgeschlagen.',
      media_e2ee_worker_ack_timeout: 'Der Medien-E2EE-Prozess hat die Einrichtung nicht rechtzeitig bestätigt.',
      media_e2ee_security_not_ready: 'Die Pair-Sicherheitsbindung ist noch nicht bestätigt.',
      media_e2ee_peer_binding_missing: 'Der bestätigte Schlüssel des Gegenübers fehlt noch.',
      media_e2ee_negotiating: 'Der Medien-E2EE-Pfad wird gerade eingerichtet.',
      media_e2ee_authentication_failed: 'Ein empfangener Medien-Frame konnte nicht authentifiziert werden.',
      microphone_permission_denied: 'Der Browser hat den Mikrofonzugriff abgelehnt.',
      publication_permission_denied: 'Der Browser hat den Kamera- oder Bildschirmzugriff abgelehnt.',
      browser_media_devices_unavailable: 'Dieses Browserfenster kann nicht auf Mediengeräte zugreifen.',
      browser_display_capture_unavailable: 'Dieser Browser unterstützt keine Bildschirmfreigabe.',
      publication_authorization_expired: 'Die Medienfreigabe ist abgelaufen.',
      remote_track_unavailable: 'Der entfernte Video-Track ist nicht mehr verfügbar.',
    } as Record<string, string>)[reasonCode] ?? 'Medienstatus der Pair-Autorität oder des Browsers:';
  }
}
