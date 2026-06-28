import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TooltipDirective } from '../directives/tooltip.directive';
import { SettingsState } from './settings-state.service';

@Component({
  selector: 'app-settings-llm',
  standalone: true,
  imports: [FormsModule, JsonPipe, TooltipDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './settings-llm.component.html',
})
export class SettingsLlmComponent {
  @Input({ required: true }) state!: SettingsState;
}
