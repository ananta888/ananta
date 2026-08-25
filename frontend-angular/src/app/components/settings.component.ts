import { Component, ViewChild } from '@angular/core';
import { ChangePasswordComponent } from './change-password.component';
import { UserManagementComponent } from './user-management.component';
import { MfaSetupComponent } from './mfa-setup.component';
import { SettingsLlmComponent } from './settings-llm.component';
import { SettingsQualityComponent } from './settings-quality.component';
import { SettingsSystemComponent } from './settings-system.component';
import { SettingsVoiceComponent } from './settings-voice.component';
import { SettingsSection, SettingsState } from './settings-state.service';
import { ModelDashboardComponent } from '../features/system/model-dashboard/model-dashboard.component';
export {
  buildOllamaModelStrategyRowsValue, buildProjectModelRoutingRecommendationValue,
  findMatchingCatalogModelId, normalizeArtifactFlowConfigValue,
  normalizeContextBundlePolicyConfigValue, normalizeHubCopilotConfigValue,
  normalizeModelOverrideMapValue, normalizeOpencodeRuntimeConfigValue,
  normalizeOpenAICompatibleBaseUrlValue, normalizeResearchBackendConfigValue,
  normalizeWorkerRuntimeConfigValue, resolveContextBundlePolicyValue,
  resolveHubCopilotModelSourceValue, resolveHubCopilotModelValue,
  resolveHubCopilotProviderSourceValue, resolveHubCopilotProviderValue,
  type OllamaStrategyRow, type ProjectModelRoutingRecommendation,
} from './settings-config.helpers';

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [
    ChangePasswordComponent, UserManagementComponent, MfaSetupComponent,
    SettingsLlmComponent, SettingsQualityComponent, SettingsSystemComponent, SettingsVoiceComponent,
    ModelDashboardComponent,
  ],
  templateUrl: './settings.component.html',
})
export class SettingsComponent extends SettingsState {
  @ViewChild(ModelDashboardComponent) private modelDashboard?: ModelDashboardComponent;

  override setSection(section: SettingsSection): void {
    if (
      this.selectedSection === 'models'
      && section !== 'models'
      && this.modelDashboard?.store.dirty()
      && !window.confirm('Ungespeicherte Modellrouting-Änderungen verwerfen?')
    ) return;
    super.setSection(section);
  }
}
