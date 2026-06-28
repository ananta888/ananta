import { Component } from '@angular/core';
import { ChangePasswordComponent } from './change-password.component';
import { UserManagementComponent } from './user-management.component';
import { MfaSetupComponent } from './mfa-setup.component';
import { SettingsLlmComponent } from './settings-llm.component';
import { SettingsQualityComponent } from './settings-quality.component';
import { SettingsSystemComponent } from './settings-system.component';
import { SettingsState } from './settings-state.service';
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
    SettingsLlmComponent, SettingsQualityComponent, SettingsSystemComponent,
  ],
  templateUrl: './settings.component.html',
})
export class SettingsComponent extends SettingsState {}
