import { ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import {
  VoiceConfiguration,
  VoiceConfigurationAdjustment,
  VoiceConfigurationField,
  VoiceConfigurationScope,
  VoiceConfigurationSchema,
} from './voice.models';
import {
  configurationFields,
  deleteAtPath,
  hasPath,
  setAtPath,
  validateVoiceField,
  valueAtPath,
  voiceError,
  voiceMutationKey,
} from './voice-ui.helpers';

@Component({
  selector: 'app-voice-configuration-editor',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-configuration-editor.component.html',
  styleUrl: './voice-settings.css',
})
export class VoiceConfigurationEditorComponent implements OnChanges {
  @Input({ required: true }) hubUrl = '';

  private readonly api = inject(VoiceApiService);
  private readonly cdr = inject(ChangeDetectorRef);

  schema: VoiceConfigurationSchema | null = null;
  configuration: VoiceConfiguration | null = null;
  scope: VoiceConfigurationScope = 'global';
  profileId = 'default';
  sessionId = '';
  draft: Record<string, unknown> = {};
  loading = false;
  saving = false;
  errorCode = '';
  errorMessage = '';
  successMessage = '';

  ngOnChanges(): void {
    if (this.hubUrl) this.loadAll();
  }

  loadAll(): void {
    this.loading = true;
    this.clearMessages();
    forkJoin({
      schema: this.api.getConfigurationSchema(this.hubUrl),
      configuration: this.api.getConfiguration(this.hubUrl, this.query()),
    }).subscribe({
      next: ({ schema, configuration }) => {
        this.schema = schema;
        this.configuration = configuration;
        this.draft = this.scopeDelta(configuration);
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  changeScope(scope: VoiceConfigurationScope): void {
    this.scope = scope;
    this.loadConfiguration();
  }

  loadConfiguration(): void {
    if (!this.validScopeId()) return;
    this.loading = true;
    this.clearMessages();
    this.api.getConfiguration(this.hubUrl, this.query()).subscribe({
      next: (configuration) => {
        this.configuration = configuration;
        this.draft = this.scopeDelta(configuration);
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  fields(): VoiceConfigurationField[] {
    return configurationFields(this.schema).filter((field) => (
      field.visible !== false && (!field.scopes?.length || field.scopes.includes(this.scope))
    ));
  }

  groups(): string[] {
    return [...new Set(this.fields().map((field) => field.group || 'Voice'))];
  }

  fieldsForGroup(group: string): VoiceConfigurationField[] {
    return this.fields().filter((field) => (field.group || 'Voice') === group);
  }

  groupDescription(group: string): string {
    return this.schema?.groups?.find((entry) => entry.id === group)?.description || '';
  }

  value(field: VoiceConfigurationField): unknown {
    if (hasPath(this.draft, field.key)) return valueAtPath(this.draft, field.key);
    if (hasPath(this.configuration?.effective, field.key)) return valueAtPath(this.configuration?.effective, field.key);
    return field.default;
  }

  setValue(field: VoiceConfigurationField, value: unknown): void {
    const typed = field.type === 'integer' ? Math.trunc(Number(value))
      : field.type === 'number' ? Number(value)
      : value;
    this.draft = setAtPath(this.draft, field.key, typed);
    this.successMessage = '';
  }

  removeOverride(field: VoiceConfigurationField): void {
    this.draft = deleteAtPath(this.draft, field.key);
  }

  hasOverride(field: VoiceConfigurationField): boolean {
    return hasPath(this.draft, field.key);
  }

  fieldError(field: VoiceConfigurationField): string | null {
    return validateVoiceField(field, this.value(field)) || this.adjustmentFor(field)?.reason_code || null;
  }

  adjustmentFor(field: VoiceConfigurationField): VoiceConfigurationAdjustment | null {
    const adjustment = this.configuration?.adjustments?.find((entry) => entry.field === field.key);
    if (!adjustment) return null;
    const value = this.value(field);
    const requested = Array.isArray(value) ? value.map(String).join(',') : String(value ?? '');
    return requested === adjustment.requested ? adjustment : null;
  }

  hasValidationErrors(): boolean {
    return this.fields().some((field) => Boolean(this.fieldError(field))) || this.combinationErrors().length > 0;
  }

  combinationErrors(): string[] {
    const primaryField = this.fields().find((field) => field.key === 'primary_backend');
    const secondaryField = this.fields().find((field) => field.key === 'secondary_backends');
    const primary = primaryField ? String(this.value(primaryField) || '') : '';
    const secondaryValue = secondaryField ? this.value(secondaryField) : [];
    const secondary = Array.isArray(secondaryValue) ? secondaryValue.map(String) : [];
    return primary && secondary.includes(primary) ? ['voice_configuration.duplicate_backend'] : [];
  }

  optionValues(field: VoiceConfigurationField): Array<{ value: unknown; label: string; disabled: boolean; reason: string }> {
    if (field.options?.length) {
      return field.options.map((option) => ({
        value: option.value,
        label: option.label || String(option.value),
        disabled: option.enabled === false,
        reason: option.reason_code || '',
      }));
    }
    return (field.enum || []).map((value) => ({ value, label: String(value), disabled: false, reason: '' }));
  }

  sourceLabel(field: VoiceConfigurationField): string {
    const sources = this.configuration?.sources;
    if (!sources) return 'default';
    if (!Array.isArray(sources)) {
      const source = sources[field.key];
      return source ? `${source.scope}${source.scope_id ? `:${source.scope_id}` : ''}` : 'default';
    }
    const match = [...sources].reverse().find((source) => (
      source.keys?.includes(field.key) || hasPath(source.delta, field.key)
    ));
    return match ? `${match.scope}${match.scope_id ? `:${match.scope_id}` : ''}` : 'default';
  }

  save(): void {
    if (!this.validScopeId() || this.hasValidationErrors() || !this.configuration) return;
    this.saving = true;
    this.clearMessages();
    this.api.saveConfiguration(this.hubUrl, {
      scope: this.scope,
      scope_id: this.scopeId() || undefined,
      delta: this.draft,
      expected_version: this.scopeVersion(),
    }, voiceMutationKey(`configuration:${this.scope}`)).subscribe({
      next: () => {
        this.api.getConfiguration(this.hubUrl, this.query()).subscribe({
          next: (configuration) => {
            this.configuration = configuration;
            this.draft = this.scopeDelta(configuration, this.draft);
            this.saving = false;
            this.successMessage = 'Voice-Konfiguration gespeichert.';
            this.cdr.markForCheck();
          },
          error: (error) => this.fail(error),
        });
      },
      error: (error) => this.fail(error),
    });
  }

  resetDelta(): void {
    this.draft = {};
    this.save();
  }

  private query(): { profileId?: string; sessionId?: string } {
    return {
      profileId: this.scope === 'global' ? undefined : this.profileId,
      sessionId: this.scope === 'session' ? this.sessionId : undefined,
    };
  }

  private scopeDelta(
    configuration: VoiceConfiguration,
    fallback: Record<string, unknown> = {},
  ): Record<string, unknown> {
    if (configuration.delta) return structuredClone(configuration.delta);
    if (!Array.isArray(configuration.sources)) return structuredClone(fallback);
    const scopeId = this.scopeId();
    const source = [...configuration.sources].reverse().find((entry) => (
      entry.scope === this.scope
      && (this.scope === 'global' || String(entry.scope_id || '') === scopeId)
      && entry.delta
    ));
    return source?.delta ? structuredClone(source.delta) : structuredClone(fallback);
  }

  private scopeVersion(): number | undefined {
    if (!Array.isArray(this.configuration?.sources)) return undefined;
    const scopeId = this.scopeId();
    const source = [...this.configuration.sources].reverse().find((entry) => (
      entry.scope === this.scope && (this.scope === 'global' || String(entry.scope_id || '') === scopeId)
    ));
    const version = Number(source?.version);
    return Number.isInteger(version) && version > 0 ? version : undefined;
  }

  private scopeId(): string {
    if (this.scope === 'profile') return this.profileId.trim();
    if (this.scope === 'session') return this.sessionId.trim();
    return '';
  }

  private validScopeId(): boolean {
    if (this.scope === 'global') return true;
    if (this.scopeId()) return true;
    this.errorCode = 'voice.configuration.scope_id_required';
    this.errorMessage = 'Für Profil- und Session-Konfiguration ist eine ID erforderlich.';
    return false;
  }

  private clearMessages(): void {
    this.errorCode = '';
    this.errorMessage = '';
    this.successMessage = '';
  }

  private fail(error: unknown): void {
    const detail = voiceError(error);
    this.loading = false;
    this.saving = false;
    this.errorCode = detail.code;
    this.errorMessage = detail.message;
    this.cdr.markForCheck();
  }
}
