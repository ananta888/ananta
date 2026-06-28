import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SettingsState } from './settings-state.service';
import { TooltipDirective } from '../directives/tooltip.directive';

@Component({
  selector: 'app-settings-quality',
  standalone: true,
  imports: [FormsModule, TooltipDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './settings-quality.component.html',
})
export class SettingsQualityComponent {
  @Input({ required: true }) state!: SettingsState;
}
