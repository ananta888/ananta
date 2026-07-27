import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SettingsState } from './settings-state.service';

@Component({
  selector: 'app-settings-system',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './settings-system.component.html',
})
export class SettingsSystemComponent {
  @Input({ required: true }) state!: SettingsState;
}
