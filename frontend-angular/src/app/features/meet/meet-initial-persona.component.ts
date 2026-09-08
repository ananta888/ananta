import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, signal } from '@angular/core';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { MeetAvatarPickerComponent } from './meet-avatar-picker.component';
import { MeetAvatarVideoPickerComponent, AvatarVideoChoice } from './meet-avatar-video-picker.component';
import { MeetVoicePickerComponent } from './meet-voice-picker.component';

type ProfilePin = PersonaEffectiveProfile['selection'];
export interface InitialPersonaChoice {
  avatar?: { mode: 'persona-image-v1'; profile: ProfilePin } |
    { mode: 'persona-video-v1'; profile: ProfilePin; repeat_mode: 'loop' | 'hold_last' };
  voice?: { profile: ProfilePin };
}

/** Passive command draft only; profile authority and dispatch belong to the Hub. */
@Component({ selector: 'app-meet-initial-persona', standalone: true,
  imports: [MeetAvatarPickerComponent, MeetAvatarVideoPickerComponent, MeetVoicePickerComponent], template: `
  <details>
    <summary>Optional: Persona vor dem Start auswählen</summary>
    <p>Der Hub prüft diese Profile vor der Auftragsübergabe erneut. Keine Vorschau, kein Medienstart und
      keine Ersatzquelle bei fehlender Freigabe. Der Avatar bleibt zunächst pausiert.</p>
    @if (images) {
      <app-meet-avatar-picker [projectId]="projectId" [disabled]="disabled" (avatarSelected)="image($event)" />
    }
    @if (videos) {
      <app-meet-avatar-video-picker [projectId]="projectId" [disabled]="disabled" (videoSelected)="video($event)" />
    }
    @if (voices) {
      <app-meet-voice-picker [projectId]="projectId" [disabled]="disabled" (voiceSelected)="voice($event)" />
    }
    <p>Startauswahl: {{ value().avatar?.mode === 'persona-video-v1' ? 'stummes Profilvideo' : value().avatar ? 'Profilbild' : 'festes KI-Symbol' }};
      {{ value().voice ? 'Profilstimme' : 'konfigurierte KI-Stimme' }}. Dies erweitert keine Quellenrechte.</p>
  </details>
` })
export class MeetInitialPersonaComponent implements OnChanges {
  @Input({ required: true }) projectId = '';
  @Input() taskId = '';
  @Input() images = false;
  @Input() videos = false;
  @Input() voices = false;
  @Input() disabled = false;
  @Output() choiceChanged = new EventEmitter<InitialPersonaChoice | null>();
  readonly value = signal<InitialPersonaChoice>({});
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['projectId'] || changes['taskId']) { this.publish({}); return; }
    const next = { ...this.value() };
    if (!this.images || !this.videos && next.avatar?.mode === 'persona-video-v1') delete next.avatar;
    if (!this.voices) delete next.voice;
    if (Object.keys(next).length !== Object.keys(this.value()).length) this.publish(next);
  }
  image(profile: ProfilePin | null): void {
    if (this.disabled || !this.images) return;
    const next = { ...this.value() }; delete next.avatar;
    if (profile) next.avatar = { mode: 'persona-image-v1', profile: { ...profile } };
    this.publish(next);
  }
  video(choice: AvatarVideoChoice): void {
    if (this.disabled || !this.images || !this.videos || !['loop', 'hold_last'].includes(choice.repeat_mode)) return;
    this.publish({ ...this.value(), avatar: { mode: 'persona-video-v1', profile: { ...choice.profile }, repeat_mode: choice.repeat_mode } });
  }
  voice(profile: ProfilePin | null): void {
    if (this.disabled || !this.voices) return;
    const next = { ...this.value() }; delete next.voice;
    if (profile) next.voice = { profile: { ...profile } };
    this.publish(next);
  }
  private publish(next: InitialPersonaChoice): void {
    this.value.set(next);
    this.choiceChanged.emit(Object.keys(next).length ? structuredClone(next) : null);
  }
}
