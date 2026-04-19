import { Component, EventEmitter, Input, Output } from '@angular/core';

export interface PresetOption {
  id: string;
  title: string;
  description?: string;
}

@Component({
  standalone: true,
  selector: 'app-preset-picker',
  template: `
    <div class="shared-preset-picker" [class.compact]="compact" [attr.aria-label]="ariaLabel">
      @for (preset of presets; track preset.id) {
        <button
          class="secondary shared-preset-chip"
          type="button"
          (click)="selectPreset.emit(preset)"
          [attr.aria-label]="actionLabel + ': ' + preset.title"
        >
          {{ preset.title }}
          @if (!compact && preset.description) {
            <span>{{ preset.description }}</span>
          }
        </button>
      }
    </div>
  `,
})
export class PresetPickerComponent {
  @Input() presets: PresetOption[] = [];
  @Input() compact = true;
  @Input() ariaLabel = 'Vorlagen';
  @Input() actionLabel = 'Vorlage einsetzen';
  @Output() selectPreset = new EventEmitter<PresetOption>();
}
