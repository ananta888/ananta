import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';
import { recommendBlueprint } from '../shared/blueprint-recommendation';
import { AppShellStateService } from '../services/app-shell-state.service';

type BlueprintRoleForm = {
  id?: string;
  name: string;
  description: string;
  template_id: string;
  sort_order: number;
  is_required: boolean;
  config: any;
};

type BlueprintArtifactForm = {
  id?: string;
  kind: string;
  title: string;
  description: string;
  sort_order: number;
  payload: any;
};

type GuidedSetupForm = {
  goal_type: string;
  strictness: string;
  domain: string;
  execution_style: string;
};

@Component({
  standalone: true,
  selector: 'app-teams',
  imports: [FormsModule, UiSkeletonComponent],
  templateUrl: './teams.component.html',
})
export class TeamsComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns = inject(NotificationService);
  private userAuth = inject(UserAuthService);
  private shellState = inject(AppShellStateService);

  currentTab: 'blueprints' | 'teams' | 'advanced' = 'blueprints';
  viewMode: 'standard' | 'admin' = 'standard';
  advancedTab: 'teams' | 'types' | 'roles' = 'teams';

  isAdmin = false;
  busy = false;
  loading = false;
  blueprints: any[] = [];
  blueprintCatalog: any[] = [];
  blueprintCatalogModel: any = null;
  teams: any[] = [];
  templates: any[] = [];
  teamTypesList: any[] = [];
  allRoles: any[] = [];
  selectedBlueprintId = '';
  guidedSetup: GuidedSetupForm = this.emptyGuidedSetup();

  readonly guidedGoalTypeOptions = [
    { value: 'new_feature', label: 'Neues Feature / Weiterentwicklung' },
    { value: 'bugfix', label: 'Bugfix / Incident' },
    { value: 'research', label: 'Research / Analyse' },
    { value: 'security_review', label: 'Security / Compliance Review' },
    { value: 'release_prep', label: 'Release-Vorbereitung' },
  ];
  readonly guidedStrictnessOptions = [
    { value: 'safe', label: 'Vorsichtig' },
    { value: 'balanced', label: 'Ausgewogen' },
    { value: 'strict', label: 'Strikt' },
  ];
  readonly guidedDomainOptions = [
    { value: 'software', label: 'Software' },
    { value: 'security', label: 'Security' },
    { value: 'release', label: 'Release' },
    { value: 'general', label: 'Allgemein' },
  ];
  readonly guidedExecutionStyleOptions = [
    { value: 'iterative', label: 'Iterativ (Sprint/Loop)' },
    { value: 'flow', label: 'Flow/Kanban' },
    { value: 'opencode', label: 'OpenCode/Execution-Kaskade' },
    { value: 'evolution', label: 'Research -> Evolution' },
  ];

  newType: any = { name: '', description: '' };
  newRole: any = { name: '', description: '', default_template_id: '' };
  newTeam: any = { id: '', name: '', team_type_id: '', description: '', members: [] as any[] };
  blueprintForm: any = this.emptyBlueprintForm();
  teamFromBlueprint: any = this.emptyTeamFromBlueprint();

  hub = this.dir.list().find(a => a.role === 'hub');
  allAgents = this.dir.list();
  private refreshSafetyTimer?: ReturnType<typeof setTimeout>;

  ngOnInit() {
    this.userAuth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
      if (this.isAdmin && this.shellState.mode() === 'advanced') this.viewMode = 'admin';
      if (!this.isAdmin) this.viewMode = 'standard';
    });
    this.refresh();
  }

  setViewMode(mode: 'standard' | 'admin') {
    this.viewMode = mode;
    if (mode === 'standard' && this.currentTab === 'advanced') this.currentTab = 'blueprints';
  }

  get selectedInstantiateBlueprint() {
    return this.blueprints.find(blueprint => blueprint.id === this.teamFromBlueprint.blueprint_id) || null;
  }

  selectedCatalogBlueprintCard() {
    if (!this.selectedBlueprintId) return null;
    return this.catalogBlueprintCards().find(blueprint => blueprint.id === this.selectedBlueprintId) || null;
  }

  refresh() {
    if (!this.hub) return;
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.loading = true;
    this.refreshSafetyTimer = setTimeout(() => {
      this.loading = false;
      this.refreshSafetyTimer = undefined;
      this.ns.info('Teams-Ansicht wurde mit Safe-Timeout entsperrt. Sie koennen weiterarbeiten.');
    }, 20000);
    let pending = 7;
    const done = () => {
      pending -= 1;
      if (pending <= 0) {
        this.loading = false;
        if (this.refreshSafetyTimer) {
          clearTimeout(this.refreshSafetyTimer);
          this.refreshSafetyTimer = undefined;
        }
      }
    };

    this.hubApi.listBlueprints(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        this.blueprints = this.normalizeListResponse(r);
        this.keepSelectionsStable();
      },
      error: () => this.ns.error('Blueprints konnten nicht geladen werden'),
    });

    this.hubApi.listBlueprintCatalog(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        const payload = r && typeof r === 'object' ? r : {};
        this.blueprintCatalog = Array.isArray((payload as any).items)
          ? (payload as any).items
          : [];
        this.blueprintCatalogModel = (payload as any).public_model || null;
      },
      error: () => {
        this.blueprintCatalog = [];
        this.blueprintCatalogModel = null;
      },
    });

    this.hubApi.listTeams(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        this.teams = this.normalizeListResponse(r);
        this.allAgents = this.dir.list();
      },
      error: () => this.ns.error('Teams konnten nicht geladen werden'),
    });

    this.hubApi.listTemplates(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.templates = this.normalizeListResponse(r)),
      error: () => this.ns.error('Rollen-Templates konnten nicht geladen werden'),
    });

    this.hubApi.listTeamTypes(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.teamTypesList = this.normalizeListResponse(r)),
      error: () => this.ns.error('Team-Typen konnten nicht geladen werden'),
    });

    this.hubApi.listTeamRoles(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.allRoles = this.normalizeListResponse(r)),
      error: () => this.ns.error('Rollen konnten nicht geladen werden'),
    });

    this.hubApi.listAgents(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        const data = Array.isArray(r) ? r : r?.items || r?.data || [];
        this.allAgents = Array.isArray(data) ? data : this.dir.list();
      },
      error: () => {
        this.allAgents = this.dir.list();
      },
    });
  }

  normalizeListResponse(value: any): any[] {
    return Array.isArray(value) ? value : [];
  }

  emptyBlueprintForm() {
    return {
      id: '',
      name: '',
      description: '',
      base_team_type_name: '',
      roles: [] as BlueprintRoleForm[],
      artifacts: [] as BlueprintArtifactForm[],
    };
  }

  emptyTeamFromBlueprint() {
    return {
      blueprint_id: '',
      name: '',
      description: '',
      activate: true,
      members: [] as any[],
    };
  }

  emptyGuidedSetup(): GuidedSetupForm {
    return {
      goal_type: 'new_feature',
      strictness: 'balanced',
      domain: 'software',
      execution_style: 'iterative',
    };
  }

  startNewBlueprint() {
    this.selectedBlueprintId = '';
    this.blueprintForm = this.emptyBlueprintForm();
  }

  selectBlueprint(blueprint: any) {
    this.selectedBlueprintId = blueprint.id;
    this.blueprintForm = {
      id: blueprint.id,
      name: blueprint.name || '',
      description: blueprint.description || '',
      base_team_type_name: blueprint.base_team_type_name || '',
      roles: (blueprint.roles || []).map((role: any) => ({
        id: role.id,
        name: role.name || '',
        description: role.description || '',
        template_id: role.template_id || '',
        sort_order: role.sort_order || 0,
        is_required: role.is_required !== false,
        config: role.config || {},
      })),
      artifacts: (blueprint.artifacts || []).map((artifact: any) => ({
        id: artifact.id,
        kind: artifact.kind || 'task',
        title: artifact.title || '',
        description: artifact.description || '',
        sort_order: artifact.sort_order || 0,
        payload: {
          status: artifact.payload?.status || 'todo',
          priority: artifact.payload?.priority || 'Medium',
        },
      })),
    };
  }

  selectBlueprintFromCatalog(blueprint: any) {
    const fullBlueprint = this.blueprints.find(item => item.id === blueprint.id);
    if (!fullBlueprint) {
      this.selectedBlueprintId = blueprint.id || '';
      return;
    }
    this.selectBlueprint(fullBlueprint);
  }

  catalogBlueprintCards(): any[] {
    if (this.blueprintCatalog.length > 0) return this.blueprintCatalog;
    return this.blueprints.map(blueprint => ({
      id: blueprint.id,
      name: blueprint.name,
      short_description: blueprint.description || '',
      intended_use: 'Reusable team definition with role setup and starter artifacts.',
      when_to_use: 'Use for repeatable team startup instead of manual assembly.',
      expected_outputs: (blueprint.artifacts || [])
        .filter((artifact: any) => String(artifact?.kind || '').toLowerCase() === 'task')
        .slice(0, 3)
        .map((artifact: any) => artifact.title)
        .filter(Boolean),
      safety_review_stance: 'balanced security, standard verification',
      is_standard_blueprint: !!blueprint.is_seed,
      entry_recommended: !!blueprint.is_seed,
    }));
  }

  formatExpectedOutputs(outputs: any): string {
    const values = Array.isArray(outputs)
      ? outputs.map(item => String(item || '').trim()).filter(Boolean)
      : [];
    return values.length ? values.join(', ') : 'Starter tasks and role-ready execution context';
  }

  formatPreviewList(values: any, fallback: string, limit = 4): string {
    const normalized = Array.isArray(values)
      ? values.map(item => String(item || '').trim()).filter(Boolean).slice(0, limit)
      : [];
    return normalized.length ? normalized.join(', ') : fallback;
  }

  previewBlueprintRoles(blueprint: any, limit = 5): string {
    const roles = Array.isArray(blueprint?.roles)
      ? blueprint.roles.map((role: any) => String(role?.name || '').trim()).filter(Boolean).slice(0, limit)
      : [];
    return roles.length ? roles.join(', ') : 'Keine Start-Rollen';
  }

  previewBlueprintStartTasks(blueprint: any, limit = 5): string {
    const tasks = Array.isArray(blueprint?.artifacts)
      ? blueprint.artifacts
        .filter((artifact: any) => String(artifact?.kind || '').toLowerCase() === 'task')
        .map((artifact: any) => String(artifact?.title || '').trim())
        .filter(Boolean)
        .slice(0, limit)
      : [];
    return tasks.length ? tasks.join(', ') : 'Keine expliziten Start-Aufgaben';
  }

  guidedSetupRecommendation() {
    const recommendation = recommendBlueprint({
      goalType: this.guidedSetup.goal_type,
      strictness: this.guidedSetup.strictness,
      domain: this.guidedSetup.domain,
      executionStyle: this.guidedSetup.execution_style,
    });
    const card = this.blueprintCardByName(recommendation.blueprintName);
    if (!card) return null;
    return {
      card,
      reasons: recommendation.reasons,
      reviewNote: recommendation.reviewNote,
      suggestedTeamName: `${card.name} Team`,
    };
  }

  applyGuidedSetupRecommendation() {
    const recommendation = this.guidedSetupRecommendation();
    if (!recommendation) {
      this.ns.error('Keine passende Blueprint-Empfehlung gefunden');
      return;
    }
    this.selectBlueprintFromCatalog(recommendation.card);
    this.currentTab = 'teams';
    this.onInstantiateBlueprintChange(recommendation.card.id);
    if (!this.teamFromBlueprint.name) this.teamFromBlueprint.name = recommendation.suggestedTeamName;
    this.ns.success(`Empfehlung gesetzt: ${recommendation.card.name}`);
  }

  blueprintLifecycleLabel(blueprint: any): string {
    if (blueprint?.is_standard_blueprint || blueprint?.is_seed) return 'Standard';
    const driftStatus = String(blueprint?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'drifted') return 'Aktualisierbar';
    return 'Angepasst';
  }

  blueprintLifecycleHint(blueprint: any): string {
    if (blueprint?.is_standard_blueprint || blueprint?.is_seed) {
      return 'Standard-Blueprint fuer den produktnahen Einstieg.';
    }
    const driftStatus = String(blueprint?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'drifted') {
      return 'Basisdefinition wurde aktualisiert; diese Variante kann abgeglichen werden.';
    }
    return 'Angepasste Blueprint-Variante fuer spezifische Teambedarfe.';
  }

  prepareInstantiateFromEditor() {
    if (!this.blueprintForm.id) return;
    this.currentTab = 'teams';
    this.onInstantiateBlueprintChange(this.blueprintForm.id);
    if (!this.teamFromBlueprint.name) {
      this.teamFromBlueprint.name = `${this.blueprintForm.name} Team`;
    }
  }

  isSeedBlueprint(id: string): boolean {
    return !!this.blueprints.find(blueprint => blueprint.id === id && blueprint.is_seed);
  }

  isSelectedSeedBlueprint(): boolean {
    return !!this.blueprintForm.id && this.isSeedBlueprint(this.blueprintForm.id);
  }

  addBlueprintRole() {
    this.blueprintForm.roles.push({
      name: '',
      description: '',
      template_id: '',
      sort_order: (this.blueprintForm.roles.length + 1) * 10,
      is_required: true,
      config: {},
    });
  }

  removeBlueprintRole(index: number) {
    this.blueprintForm.roles.splice(index, 1);
  }

  addBlueprintArtifact() {
    this.blueprintForm.artifacts.push({
      kind: 'task',
      title: '',
      description: '',
      sort_order: (this.blueprintForm.artifacts.length + 1) * 10,
      payload: { status: 'todo', priority: 'Medium' },
    });
  }

  removeBlueprintArtifact(index: number) {
    this.blueprintForm.artifacts.splice(index, 1);
  }

  saveBlueprint() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;

    const payload = {
      name: this.blueprintForm.name?.trim(),
      description: this.blueprintForm.description || undefined,
      base_team_type_name: this.blueprintForm.base_team_type_name || undefined,
      roles: this.blueprintForm.roles.map((role: BlueprintRoleForm) => ({
        name: role.name?.trim(),
        description: role.description || undefined,
        template_id: role.template_id || undefined,
        sort_order: Number(role.sort_order || 0),
        is_required: role.is_required !== false,
        config: role.config || {},
      })),
      artifacts: this.blueprintForm.artifacts.map((artifact: BlueprintArtifactForm) => ({
        kind: artifact.kind || 'task',
        title: artifact.title?.trim(),
        description: artifact.description || undefined,
        sort_order: Number(artifact.sort_order || 0),
        payload: artifact.payload || {},
      })),
    };

    this.busy = true;
    const request$ = this.blueprintForm.id
      ? this.hubApi.patchBlueprint(this.hub.url, this.blueprintForm.id, payload)
      : this.hubApi.createBlueprint(this.hub.url, payload);

    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success(this.blueprintForm.id ? 'Blueprint gespeichert' : 'Blueprint erstellt');
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Blueprint konnte nicht gespeichert werden'),
    });
  }

  deleteBlueprint(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Blueprint wirklich loeschen?')) return;
    this.busy = true;
    this.hubApi.deleteBlueprint(this.hub.url, id).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Blueprint geloescht');
        this.startNewBlueprint();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Blueprint konnte nicht geloescht werden'),
    });
  }

  onInstantiateBlueprintChange(blueprintId: string) {
    this.teamFromBlueprint.blueprint_id = blueprintId;
    this.teamFromBlueprint.members = [];
    const blueprint = this.blueprints.find(item => item.id === blueprintId);
    if (!blueprint) return;
    this.selectedBlueprintId = blueprint.id;
    if (!this.teamFromBlueprint.name) this.teamFromBlueprint.name = `${blueprint.name} Team`;
    if (!this.teamFromBlueprint.description) this.teamFromBlueprint.description = blueprint.description || '';
  }

  addInstantiateMember() {
    this.teamFromBlueprint.members.push({ blueprint_role_id: '', agent_url: '', custom_template_id: '' });
  }

  removeInstantiateMember(index: number) {
    this.teamFromBlueprint.members.splice(index, 1);
  }

  instantiateBlueprint() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !this.teamFromBlueprint.blueprint_id) return;

    const payload = {
      name: this.teamFromBlueprint.name?.trim(),
      description: this.teamFromBlueprint.description || undefined,
      activate: !!this.teamFromBlueprint.activate,
      members: this.teamFromBlueprint.members
        .filter((member: any) => member.agent_url || member.blueprint_role_id || member.custom_template_id)
        .map((member: any) => ({
          agent_url: member.agent_url,
          blueprint_role_id: member.blueprint_role_id || undefined,
          custom_template_id: member.custom_template_id || undefined,
        })),
    };

    this.busy = true;
    this.hubApi.instantiateBlueprint(this.hub.url, this.teamFromBlueprint.blueprint_id, payload)
      .pipe(finalize(() => (this.busy = false)))
      .subscribe({
        next: () => {
          this.ns.success('Team aus Blueprint erstellt');
          this.teamFromBlueprint = this.emptyTeamFromBlueprint();
          this.refresh();
        },
        error: (err) => this.handleTeamError(err, 'Team konnte nicht aus Blueprint erstellt werden'),
      });
  }

  useSeedBlueprint(name: string) {
    const blueprint = this.blueprints.find(item => item.name === name);
    if (!blueprint) {
      this.ns.error(`Seed-Blueprint nicht gefunden: ${name}`);
      return;
    }
    this.currentTab = 'teams';
    this.teamFromBlueprint = this.emptyTeamFromBlueprint();
    this.teamFromBlueprint.name = `${name} Team`;
    this.onInstantiateBlueprintChange(blueprint.id);
  }

  prepareTeamEdit(team: any) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    this.currentTab = 'advanced';
    this.advancedTab = 'teams';
    this.newTeam = {
      id: team.id,
      name: team.name || '',
      description: team.description || '',
      team_type_id: team.team_type_id || '',
      members: (team.members || []).map((member: any) => ({ ...member })),
    };
  }

  resetForm() {
    this.newTeam = { id: '', name: '', team_type_id: '', description: '', members: [] };
  }

  addManualMember() {
    this.newTeam.members.push({ agent_url: '', role_id: '', custom_template_id: '' });
  }

  createTeamType() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createTeamType(this.hub.url, this.newType).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Team-Typ erstellt');
        this.newType = { name: '', description: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen des Team-Typs'),
    });
  }

  deleteTeamType(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team-Typ wirklich loeschen?')) return;
    this.hubApi.deleteTeamType(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team-Typ geloescht');
        this.refresh();
      },
    });
  }

  createRole() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createRole(this.hub.url, this.newRole).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Rolle erstellt');
        this.newRole = { name: '', description: '', default_template_id: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen der Rolle'),
    });
  }

  deleteRole(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Rolle wirklich loeschen?')) return;
    this.hubApi.deleteRole(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Rolle geloescht');
        this.refresh();
      },
    });
  }

  toggleRoleForType(typeId: string, roleId: string, isLinked: boolean) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    const request$ = isLinked ? this.hubApi.unlinkRoleFromType(this.hub.url, typeId, roleId) : this.hubApi.linkRoleToType(this.hub.url, typeId, roleId);
    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Aenderung konnte nicht gespeichert werden'),
    });
  }

  isRoleLinked(type: any, roleId: string): boolean {
    return type.role_ids && type.role_ids.includes(roleId);
  }

  getRolesForType(typeId: string): any[] {
    if (!typeId) return this.allRoles;
    const type = this.teamTypesList.find(t => t.id === typeId);
    if (!type || !type.role_ids || !type.role_ids.length) return this.allRoles;
    return this.allRoles.filter(role => type.role_ids.includes(role.id));
  }

  getRoleTemplateMapping(typeId: string, roleId: string): string {
    const type = this.teamTypesList.find(t => t.id === typeId);
    return type?.role_templates?.[roleId] || '';
  }

  setRoleTemplateMapping(typeId: string, roleId: string, templateId: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.hubApi.updateRoleTemplateMapping(this.hub.url, typeId, roleId, templateId || null).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Rollen-Template-Zuordnung konnte nicht gespeichert werden'),
    });
  }

  createTeam() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    const payload = {
      name: this.newTeam.name,
      description: this.newTeam.description,
      team_type_id: this.newTeam.team_type_id || undefined,
      members: (this.newTeam.members || []).map((member: any) => ({
        agent_url: member.agent_url,
        role_id: member.role_id,
        custom_template_id: member.custom_template_id || undefined,
      })),
    };

    this.busy = true;
    const request$ = this.newTeam.id ? this.hubApi.patchTeam(this.hub.url, this.newTeam.id, payload) : this.hubApi.createTeam(this.hub.url, payload);
    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success(this.newTeam.id ? 'Team aktualisiert' : 'Team erstellt');
        this.resetForm();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Fehler beim Speichern'),
    });
  }

  activate(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.hubApi.activateTeam(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team aktiviert');
        this.refresh();
      },
    });
  }

  deleteTeam(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team wirklich loeschen?')) return;
    this.hubApi.deleteTeam(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team geloescht');
        this.refresh();
      },
    });
  }

  availableAgents(team: any) {
    const memberUrls = (team.members || []).map((member: any) => member.agent_url);
    return this.allAgents.filter(agent => !memberUrls.includes(agent.url) && agent.role !== 'hub');
  }

  availableBlueprintAgents() {
    const selectedUrls = this.teamFromBlueprint.members.map((member: any) => member.agent_url).filter(Boolean);
    return this.allAgents.filter(agent => !selectedUrls.includes(agent.url) && agent.role !== 'hub');
  }

  removeMemberFromForm(index: number) {
    this.newTeam.members.splice(index, 1);
  }

  getAgentNameByUrl(url: string): string {
    return this.allAgents.find(agent => agent.url === url)?.name || url;
  }

  getTeamTypeName(id: string): string {
    return this.teamTypesList.find(type => type.id === id)?.name || 'Kein Typ';
  }

  getRoleName(id: string): string {
    return this.allRoles.find(role => role.id === id)?.name || 'Keine Rolle';
  }

  getBlueprintName(id: string): string {
    return this.blueprints.find(blueprint => blueprint.id === id)?.name || '';
  }

  getBlueprintRoleName(team: any, blueprintRoleId: string): string {
    const role = team?.blueprint_snapshot?.roles?.find((item: any) => item.id === blueprintRoleId);
    return role?.name || 'Blueprint-Rolle';
  }

  teamLifecycleLabel(team: any): string {
    const state = team?.user_lifecycle_state;
    if (state?.label) return String(state.label);
    const driftStatus = String(team?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'in_sync') return 'Standard';
    if (driftStatus === 'drifted') return 'Aktualisierbar';
    return 'Angepasst';
  }

  teamLifecycleHint(team: any): string {
    const state = team?.user_lifecycle_state;
    if (state?.hint) return String(state.hint);
    const driftStatus = String(team?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'in_sync') return 'Dieses Team folgt dem aktuellen Blueprint-Stand.';
    if (driftStatus === 'drifted') return 'Blueprint wurde aktualisiert; diese Instanz nutzt noch den vorherigen Stand.';
    return 'Diese laufende Team-Instanz wurde individuell angepasst.';
  }

  keepSelectionsStable() {
    if (this.selectedBlueprintId) {
      const blueprint = this.blueprints.find(item => item.id === this.selectedBlueprintId);
      if (blueprint) this.selectBlueprint(blueprint);
    }
    if (this.teamFromBlueprint.blueprint_id && !this.blueprints.find(item => item.id === this.teamFromBlueprint.blueprint_id)) {
      this.teamFromBlueprint = this.emptyTeamFromBlueprint();
    }
  }

  private handleTeamError(err: any, fallback: string) {
    const message = err?.error?.message;
    const data = err?.error?.data || {};
    const hints: Record<string, string> = {
      team_type_not_found: 'Team-Typ nicht gefunden.',
      role_not_found: data.role_id ? `Rolle nicht gefunden: ${data.role_id}` : 'Rolle nicht gefunden.',
      invalid_role_for_team_type: data.role_id ? `Rolle nicht erlaubt: ${data.role_id}` : 'Rolle nicht fuer Team-Typ erlaubt.',
      template_not_found: data.template_id ? `Rollen-Template nicht gefunden: ${data.template_id}` : 'Rollen-Template nicht gefunden.',
      role_id_required: 'Rollen-ID erforderlich.',
      blueprint_in_use: data.team_count ? `Blueprint wird noch von ${data.team_count} Team(s) verwendet.` : 'Blueprint wird noch verwendet.',
      blueprint_name_exists: 'Ein Blueprint mit diesem Namen existiert bereits.',
      blueprint_name_required: 'Blueprint-Name ist erforderlich.',
      blueprint_role_not_found: data.blueprint_role_id ? `Blueprint-Rolle nicht gefunden: ${data.blueprint_role_id}` : 'Blueprint-Rolle nicht gefunden.',
      duplicate_blueprint_role_name: data.role_name ? `Blueprint-Rolle doppelt: ${data.role_name}` : 'Blueprint-Rollen muessen eindeutig sein.',
      duplicate_blueprint_role_sort_order: data.sort_order ? `Blueprint-Rollen-Sortierung doppelt: ${data.sort_order}` : 'Blueprint-Rollen-Sortierungen muessen eindeutig sein.',
      blueprint_role_name_required: 'Jede Blueprint-Rolle benoetigt einen Namen.',
      duplicate_blueprint_artifact_title: data.title ? `Blueprint-Artefakt doppelt: ${data.title}` : 'Blueprint-Artefakt-Titel muessen eindeutig sein.',
      duplicate_blueprint_artifact_sort_order: data.sort_order ? `Blueprint-Artefakt-Sortierung doppelt: ${data.sort_order}` : 'Blueprint-Artefakt-Sortierungen muessen eindeutig sein.',
      blueprint_artifact_title_required: 'Jedes Blueprint-Artefakt benoetigt einen Titel.',
      blueprint_artifact_kind_required: 'Jedes Blueprint-Artefakt benoetigt einen Typ.',
      blueprint_artifact_kind_invalid: data.kind ? `Blueprint-Artefakt-Typ ungueltig: ${data.kind}` : 'Blueprint-Artefakt-Typ ungueltig.',
    };
    if (message && hints[message]) {
      this.ns.error(hints[message]);
      return;
    }
    if (message) {
      this.ns.error(message);
      return;
    }
    this.ns.error(fallback);
  }

  private blueprintCardByName(name: string) {
    const normalizedTarget = String(name || '').trim().toLowerCase();
    return this.catalogBlueprintCards().find(card => String(card?.name || '').trim().toLowerCase() === normalizedTarget) || null;
  }
}
