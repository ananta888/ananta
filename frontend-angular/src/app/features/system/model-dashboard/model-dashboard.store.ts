import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, forkJoin, of } from 'rxjs';

import { NotificationService } from '../../../services/notification.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { SystemFacade } from '../../system/system.facade';
import {
  EffectiveModelRoute,
  ModelCatalog,
  ModelCatalogClient,
  ModelCatalogV2,
  ModelConsumer,
  ModelFallbackCandidate,
  ModelInventoryDescriptor,
  ModelRoutingConfiguration,
  ModelRoutingTemplate,
  ModelRoutingValidationReport,
  ModelSummary,
  canUseModelMutation,
} from './model-catalog.client';

export type ModelDashboardTab = 'overview' | 'assignments' | 'fallbacks' | 'changes';

function cloneRouting(value: ModelRoutingConfiguration): ModelRoutingConfiguration {
  return JSON.parse(JSON.stringify(value)) as ModelRoutingConfiguration;
}

function emptyCandidate(profileId: string): ModelFallbackCandidate {
  return {
    profile_id: profileId,
    retry_budget: 0,
    triggers: [],
    max_context_tokens: null,
    max_estimated_cost_per_step: null,
    requires_tools: false,
    requires_json: false,
    cloud_allowed: false,
  };
}

@Injectable()
export class ModelDashboardStore {
  private readonly client = inject(ModelCatalogClient);
  private readonly system = inject(SystemFacade);
  private readonly auth = inject(UserAuthService);
  private readonly notifications = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);
  private baseUrl = '';

  readonly activeTab = signal<ModelDashboardTab>('overview');
  readonly catalog = signal<ModelCatalog | null>(null);
  readonly inventory = signal<ModelCatalogV2 | null>(null);
  readonly consumers = signal<readonly ModelConsumer[]>([]);
  readonly authoritativeRouting = signal<ModelRoutingConfiguration | null>(null);
  readonly draftRouting = signal<ModelRoutingConfiguration | null>(null);
  readonly validation = signal<ModelRoutingValidationReport | null>(null);
  readonly effectiveRoute = signal<EffectiveModelRoute | null>(null);
  readonly templates = signal<readonly ModelRoutingTemplate[]>([]);
  readonly importPreview = signal<Record<string, unknown> | null>(null);
  readonly pendingImport = signal<ModelRoutingConfiguration | null>(null);
  readonly conflictRevision = signal<number | null>(null);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly error = signal('');
  readonly search = signal('');
  readonly runtimeFilter = signal('all');
  readonly availabilityFilter = signal('all');
  readonly sourceFilter = signal('all');
  readonly user = signal<unknown>(this.auth.userPayload);

  readonly canRefresh = computed(() => canUseModelMutation(this.user(), 'model_catalog.refresh'));
  readonly canSetDefault = computed(() => canUseModelMutation(this.user(), 'model_catalog.set_default'));
  readonly canValidate = computed(() => canUseModelMutation(this.user(), 'model_routing.validate'));
  readonly canExport = computed(() => canUseModelMutation(this.user(), 'model_routing.export'));
  readonly canMutate = computed(() => canUseModelMutation(this.user(), 'model_routing.mutate'));
  readonly dirty = computed(() => JSON.stringify(this.authoritativeRouting()) !== JSON.stringify(this.draftRouting()));
  readonly filteredModels = computed(() => {
    const search = this.search().trim().toLocaleLowerCase();
    return (this.inventory()?.models ?? []).filter(model => {
      const searchable = [
        model.display_name, model.model_id, model.provider_id, model.executor_id,
        ...model.aliases, ...model.profile_ids,
      ].join(' ').toLocaleLowerCase();
      return (!search || searchable.includes(search))
        && (this.runtimeFilter() === 'all' || model.runtime === this.runtimeFilter())
        && (this.availabilityFilter() === 'all' || model.availability === this.availabilityFilter())
        && (this.sourceFilter() === 'all' || model.source_kinds.includes(this.sourceFilter()));
    });
  });
  readonly visibleModels = computed(() => this.filteredModels().slice(0, 500));
  readonly profileOptions = computed(() => {
    const options = new Map<string, ModelInventoryDescriptor>();
    for (const model of this.inventory()?.models ?? []) {
      for (const profileId of model.profile_ids) options.set(profileId, model);
    }
    return [...options.entries()].map(([profileId, model]) => ({ profileId, model }));
  });
  readonly consumerCategories = computed(() => {
    const groups = new Map<string, ModelConsumer[]>();
    for (const consumer of this.consumers()) {
      groups.set(consumer.category, [...(groups.get(consumer.category) ?? []), consumer]);
    }
    return [...groups.entries()].map(([category, values]) => ({ category, consumers: values }));
  });

  constructor() {
    this.auth.user$.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(user => this.user.set(user));
  }

  load(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) {
      this.error.set('Kein Hub-Agent konfiguriert.');
      return;
    }
    this.baseUrl = hub.url;
    this.loading.set(true);
    forkJoin({
      inventory: this.client.readInventory(this.baseUrl).pipe(catchError(() => of(null))),
      legacy: this.client.read(this.baseUrl).pipe(catchError(() => of(null))),
      consumers: this.client.readConsumers(this.baseUrl).pipe(catchError(() => of(null))),
      routing: this.client.readRouting(this.baseUrl).pipe(catchError(() => of(null))),
      templates: this.client.readRoutingTemplates(this.baseUrl).pipe(catchError(() => of(null))),
    }).subscribe(({ inventory, legacy, consumers, routing, templates }) => {
      this.inventory.set(inventory);
      this.catalog.set(legacy);
      this.consumers.set(consumers?.consumers ?? []);
      this.templates.set(templates?.templates ?? []);
      if (routing) {
        this.authoritativeRouting.set(cloneRouting(routing));
        this.draftRouting.set(cloneRouting(routing));
      }
      this.error.set(!inventory && !legacy ? 'Modellkatalog konnte nicht geladen werden.' : '');
      this.loading.set(false);
    });
  }

  refresh(): void {
    if (!this.baseUrl || !this.canRefresh()) return;
    this.loading.set(true);
    this.client.refreshInventory(this.baseUrl).subscribe({
      next: catalog => {
        this.inventory.set(catalog);
        this.loading.set(false);
        this.notifications.success('Modellbestand aktualisiert');
      },
      error: () => {
        this.error.set('Modellbestand konnte nicht aktualisiert werden.');
        this.loading.set(false);
      },
    });
  }

  setDefault(model: ModelSummary): void {
    if (!this.baseUrl || !this.canSetDefault() || model.availability !== 'available') return;
    this.client.selectDefault(this.baseUrl, model.provider_id, model.model_id).subscribe({
      next: () => {
        this.notifications.success(`${model.display_name} ist jetzt Standard`);
        this.load();
      },
      error: () => this.error.set('Standardmodell konnte nicht geändert werden.'),
    });
  }

  globalAssignment(consumerId: string) {
    return this.draftRouting()?.assignments.find(
      item => item.consumer_id === consumerId && item.scope === 'global' && item.scope_id === 'global',
    );
  }

  setConsumerMode(consumerId: string, mode: 'inherit' | 'profile' | 'disabled', profileId = ''): void {
    this.updateDraft(draft => {
      const previous = draft.assignments.find(
        item => item.consumer_id === consumerId && item.scope === 'global' && item.scope_id === 'global',
      );
      draft.assignments = draft.assignments.filter(
        item => !(item.consumer_id === consumerId && item.scope === 'global' && item.scope_id === 'global'),
      );
      draft.assignments.push({
        consumer_id: consumerId,
        scope: 'global',
        scope_id: 'global',
        mode,
        profile_id: mode === 'profile' ? profileId : null,
        provider_id: null,
        model_id: null,
        fallback_group_id: previous?.fallback_group_id ?? null,
      });
    });
  }

  setConsumerFallback(consumerId: string, groupId: string): void {
    const current = this.globalAssignment(consumerId);
    this.setConsumerMode(
      consumerId,
      current?.mode === 'profile' ? 'profile' : current?.mode === 'disabled' ? 'disabled' : 'inherit',
      current?.profile_id ?? '',
    );
    this.updateDraft(draft => {
      const assignment = draft.assignments.find(
        item => item.consumer_id === consumerId && item.scope === 'global',
      );
      if (assignment) assignment.fallback_group_id = groupId || null;
    });
  }

  addFallbackGroup(groupId: string): void {
    const normalized = groupId.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$/.test(normalized)) {
      this.error.set('Ungültige Fallbackgruppen-ID.');
      return;
    }
    this.updateDraft(draft => {
      if (draft.fallback_groups.some(group => group.group_id === normalized)) return;
      draft.fallback_groups.push({
        group_id: normalized,
        candidates: [],
        stop_on_policy_block: true,
        max_total_retries: 0,
        on_exhausted: 'stop',
        escalation_profile_id: null,
      });
    });
  }

  removeFallbackGroup(groupId: string): void {
    this.updateDraft(draft => {
      draft.fallback_groups = draft.fallback_groups.filter(group => group.group_id !== groupId);
      for (const assignment of draft.assignments) {
        if (assignment.fallback_group_id === groupId) assignment.fallback_group_id = null;
      }
    });
  }

  addCandidate(groupId: string, profileId: string): void {
    if (!profileId) return;
    this.updateGroup(groupId, group => {
      if (!group.candidates.some(item => item.profile_id === profileId)) {
        group.candidates.push(emptyCandidate(profileId));
      }
    });
  }

  removeCandidate(groupId: string, index: number): void {
    this.updateGroup(groupId, group => group.candidates.splice(index, 1));
  }

  moveCandidate(groupId: string, index: number, delta: number): void {
    this.updateGroup(groupId, group => {
      const target = index + delta;
      if (target < 0 || target >= group.candidates.length) return;
      const [candidate] = group.candidates.splice(index, 1);
      group.candidates.splice(target, 0, candidate);
    });
  }

  setCandidateRetry(groupId: string, index: number, retryBudget: number): void {
    this.updateGroup(groupId, group => {
      group.candidates[index].retry_budget = Math.max(0, Math.min(8, retryBudget || 0));
    });
  }

  setCandidateTriggers(groupId: string, index: number, triggers: string): void {
    this.updateGroup(groupId, group => {
      group.candidates[index].triggers = [...new Set(triggers.split(',')
        .map(value => value.trim()).filter(Boolean))];
    });
  }

  setCandidateLimit(
    groupId: string,
    index: number,
    field: 'max_context_tokens' | 'max_estimated_cost_per_step',
    value: number | string | null,
  ): void {
    this.updateGroup(groupId, group => {
      if (value === '' || value === null || value === undefined) {
        group.candidates[index][field] = null;
        return;
      }
      const normalized = Number(value);
      group.candidates[index][field] = Number.isFinite(normalized)
        ? Math.max(field === 'max_context_tokens' ? 1 : 0, normalized)
        : null;
    });
  }

  setCandidateRequirement(
    groupId: string,
    index: number,
    field: 'requires_tools' | 'requires_json' | 'cloud_allowed',
    value: boolean,
  ): void {
    this.updateGroup(groupId, group => {
      group.candidates[index][field] = value;
    });
  }

  setGroupRetries(groupId: string, retries: number): void {
    this.updateGroup(groupId, group => {
      group.max_total_retries = Math.max(0, Math.min(64, retries || 0));
    });
  }

  setGroupExhaustion(groupId: string, mode: 'stop' | 'escalate', profileId = ''): void {
    this.updateGroup(groupId, group => {
      group.on_exhausted = mode;
      group.escalation_profile_id = mode === 'escalate' ? profileId || null : null;
    });
  }

  dryRun(consumerId: string): void {
    if (!this.baseUrl) return;
    this.client.dryRun(this.baseUrl, consumerId, this.draftRouting() ?? undefined).subscribe({
      next: route => this.effectiveRoute.set(route),
      error: () => this.error.set('Effektive Route konnte nicht berechnet werden.'),
    });
  }

  validate(): void {
    const draft = this.draftRouting();
    if (!draft || !this.baseUrl || !this.canValidate()) return;
    this.client.validateRouting(this.baseUrl, draft).subscribe({
      next: report => this.validation.set(report),
      error: () => this.error.set('Routing-Konfiguration ist syntaktisch ungültig.'),
    });
  }

  save(): void {
    const draft = this.draftRouting();
    if (!draft || !this.baseUrl || !this.canMutate()) return;
    this.saving.set(true);
    this.client.validateRouting(this.baseUrl, draft).subscribe({
      next: report => {
        this.validation.set(report);
        if (!report.valid) {
          this.saving.set(false);
          return;
        }
        this.client.saveRouting(this.baseUrl, draft).subscribe({
          next: saved => {
            this.authoritativeRouting.set(cloneRouting(saved));
            this.draftRouting.set(cloneRouting(saved));
            this.conflictRevision.set(null);
            this.saving.set(false);
            this.notifications.success(`Modellrouting Revision ${saved.revision} gespeichert`);
          },
          error: error => {
            this.conflictRevision.set(Number(error?.error?.data?.current_revision) || null);
            this.error.set(error?.status === 409
              ? 'Konflikt: Die Hub-Konfiguration wurde zwischenzeitlich geändert.'
              : 'Routing-Konfiguration konnte nicht gespeichert werden.');
            this.saving.set(false);
          },
        });
      },
      error: () => {
        this.error.set('Routing-Konfiguration konnte nicht validiert werden.');
        this.saving.set(false);
      },
    });
  }

  resetDraft(): void {
    const authoritative = this.authoritativeRouting();
    if (authoritative) this.draftRouting.set(cloneRouting(authoritative));
    this.validation.set(null);
    this.conflictRevision.set(null);
  }

  applyTemplate(templateId: string): void {
    const template = this.templates().find(item => item.template_id === templateId);
    const authoritative = this.authoritativeRouting();
    if (!template || !template.applicable || !authoritative) return;
    this.draftRouting.set(cloneRouting({
      ...template.configuration,
      revision: authoritative.revision,
    }));
    this.validation.set(null);
    this.effectiveRoute.set(null);
    this.importPreview.set(null);
  }

  exportRouting(): void {
    if (!this.baseUrl || !this.canExport()) return;
    this.client.exportRouting(this.baseUrl).subscribe(bundle => {
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `ananta-model-routing-r${bundle.configuration.revision}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    });
  }

  previewImported(value: unknown): void {
    const payload = value as { configuration?: ModelRoutingConfiguration };
    const imported = payload?.configuration;
    const current = this.authoritativeRouting();
    if (!imported || !current || !this.baseUrl || !this.canValidate()) {
      this.error.set('Importdatei enthält keinen gültigen Routing-Export.');
      return;
    }
    this.client.previewImport(this.baseUrl, imported, current.revision).subscribe({
      next: preview => {
        this.pendingImport.set(imported);
        this.importPreview.set(preview);
      },
      error: () => this.error.set('Import konnte nicht validiert werden.'),
    });
  }

  applyImported(): void {
    const imported = this.pendingImport();
    const current = this.authoritativeRouting();
    const preview = this.importPreview();
    const digest = String(preview?.['confirmation_digest'] ?? '');
    if (!imported || !current || !digest || !this.baseUrl || !this.canMutate()) return;
    this.client.applyImport(this.baseUrl, imported, current.revision, digest).subscribe({
      next: saved => {
        this.authoritativeRouting.set(cloneRouting(saved));
        this.draftRouting.set(cloneRouting(saved));
        this.pendingImport.set(null);
        this.importPreview.set(null);
        this.notifications.success(`Routing-Import als Revision ${saved.revision} angewandt`);
      },
      error: error => this.error.set(error?.status === 409
        ? 'Import-Konflikt: Bitte aktuellen Stand neu laden und erneut vergleichen.'
        : 'Routing-Import konnte nicht angewandt werden.'),
    });
  }

  private updateGroup(
    groupId: string,
    update: (group: ModelRoutingConfiguration['fallback_groups'][number]) => void,
  ): void {
    this.updateDraft(draft => {
      const group = draft.fallback_groups.find(value => value.group_id === groupId);
      if (group) update(group);
    });
  }

  private updateDraft(update: (draft: ModelRoutingConfiguration) => void): void {
    const current = this.draftRouting();
    if (!current) return;
    const draft = cloneRouting(current);
    update(draft);
    this.draftRouting.set(draft);
    this.validation.set(null);
    this.effectiveRoute.set(null);
  }
}
