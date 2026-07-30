import { Page, Route } from '@playwright/test';

export const SOURCE_CONTROL_TEST_SUPPORT_BASE = '/__test-support/source-control';
export const SOURCE_CONTROL_TEST_SUPPORT_CONTRACT = 'ananta.e2e.source-control-test-support.v1';

const FIXED_TIME = '2026-07-30T08:00:00Z';
const FIXED_EXPIRY = '2026-07-30T09:00:00Z';

type JsonRecord = Record<string, unknown>;

interface ConnectionState {
  connection: JsonRecord;
  revisionIds: string[];
}

interface IndexState {
  index_id: string;
  connection_id: string;
  source_revision_id: string;
  status: 'queued' | 'indexing' | 'ready' | 'stale';
  progress_percent: number;
  reads: number;
}

interface ApprovalState {
  approval_id: string;
  source_revision_id: string;
  destination_id: string;
  model_id: string;
  consumed: boolean;
}

interface DestinationState {
  descriptor: Readonly<JsonRecord>;
  governanceProfile: 'local_allow' | 'runtime_deny' | 'single_use_approval';
  approvalRequired: boolean;
  presetRef: string;
}

export async function installSourceControlTestSupport(page: Page): Promise<void> {
  const state = new DeterministicSourceControlState();
  await page.route(`**${SOURCE_CONTROL_TEST_SUPPORT_BASE}/**`, async (route) => {
    await state.handle(route);
  });
}

class DeterministicSourceControlState {
  private ordinal = 1;
  private readonly connections = new Map<string, ConnectionState>();
  private readonly revisions = new Map<string, JsonRecord>();
  private readonly indexes = new Map<string, IndexState>();
  private readonly activeIndexByConnection = new Map<string, string>();
  private readonly approvals = new Map<string, ApprovalState>();
  private readonly auditEvents: JsonRecord[] = [];

  private readonly workspace = Object.freeze({
    workspace_id: 'workspace_registered_main',
    label: 'Registered main workspace',
    repository: true,
    relative_paths: ['.', 'src'],
  });
  private readonly githubInstallation = Object.freeze({
    installation_id: 'installation_test_support',
    account_login: 'ananta-test-support',
  });
  private readonly githubRepository = Object.freeze({
    repository_id: 'repository_test_support',
    installation_id: this.githubInstallation.installation_id,
    full_name: 'ananta-test-support/demo',
    head_commit: '1111111111111111111111111111111111111111',
    next_commit: '2222222222222222222222222222222222222222',
  });
  private readonly destinations = Object.freeze([
    this.destination({
      worker_id: 'worker-local',
      worker_kind: 'native_ananta_worker',
      runtime_id: 'runtime-local',
      runtime_kind: 'docker_container',
      provider_id: 'ollama',
      model_id: 'qwen2.5-coder:7b',
      model_class: 'code',
      provider_location: 'local_container',
      data_residency: 'device-local',
      approval_required: false,
      governance_profile: 'local_allow',
      preset_ref: 'test-support.local-analysis',
    }),
    this.destination({
      worker_id: 'worker-claude-external',
      worker_kind: 'remote_worker',
      runtime_id: 'runtime-claude-external',
      runtime_kind: 'cloud_worker',
      provider_id: 'anthropic',
      model_id: 'claude-3-7-sonnet-20250219',
      model_class: 'reasoning',
      provider_location: 'external_region',
      data_residency: 'us-external',
      approval_required: false,
      governance_profile: 'runtime_deny',
      preset_ref: 'test-support.runtime-deny',
    }),
    this.destination({
      worker_id: 'worker-claude-approved',
      worker_kind: 'remote_worker',
      runtime_id: 'runtime-claude-private',
      runtime_kind: 'remote_http_worker',
      provider_id: 'anthropic',
      model_id: 'claude-3-5-haiku-20241022',
      model_class: 'reasoning',
      provider_location: 'tenant_region',
      data_residency: 'eu-tenant',
      approval_required: true,
      governance_profile: 'single_use_approval',
      preset_ref: 'test-support.single-use-approval',
    }),
  ]);

  async handle(route: Route): Promise<void> {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice(SOURCE_CONTROL_TEST_SUPPORT_BASE.length) || '/';
    const method = request.method().toUpperCase();
    const body = this.body(route);

    if (method === 'GET' && path === '/capabilities') {
      return this.ok(route, {
        capabilities: {
          workspace_vertical: true,
          github_vertical: true,
          governance_preview: true,
          approval_single_use: true,
          index_rollback: true,
        },
      });
    }
    if (method === 'GET' && path === '/workspaces') {
      return this.ok(route, { items: [this.workspace], count: 1 });
    }
    if (method === 'POST' && path === '/workspace/scan') {
      if (
        body['workspace_id'] !== this.workspace.workspace_id
        || !this.workspace.relative_paths.includes(String(body['relative_path']) as '.' | 'src')
      ) return this.error(route, 422, 'test_support_workspace_selection_invalid');
      const pair = this.createSourcePair('registered_workspace', 'Registered main workspace', 'workspace-rev-1');
      return this.ok(route, {
        connection: pair.connection,
        revision: pair.revision,
        scan: { status: 'completed', files_scanned: 42, relative_path: body['relative_path'] },
      });
    }
    if (method === 'GET' && path === '/github/installations') {
      return this.ok(route, { items: [this.githubInstallation], count: 1 });
    }
    const repositoriesMatch = path.match(/^\/github\/installations\/([^/]+)\/repositories$/);
    if (method === 'GET' && repositoriesMatch) {
      if (repositoriesMatch[1] !== this.githubInstallation.installation_id) {
        return this.error(route, 404, 'test_support_installation_not_found');
      }
      return this.ok(route, { items: [this.githubRepository], count: 1 });
    }
    if (method === 'POST' && path === '/github/scan') {
      if (
        body['installation_id'] !== this.githubInstallation.installation_id
        || body['repository_id'] !== this.githubRepository.repository_id
        || body['commit'] !== this.githubRepository.head_commit
      ) return this.error(route, 422, 'test_support_github_selection_invalid');
      const pair = this.createSourcePair('github', this.githubRepository.full_name, String(body['commit']));
      return this.ok(route, {
        connection: pair.connection,
        revision: pair.revision,
        scan: { status: 'completed', commit: body['commit'] },
      });
    }
    if (method === 'POST' && path === '/indexes') {
      const revision = this.revisions.get(String(body['source_revision_id'] || ''));
      if (!revision) return this.error(route, 404, 'test_support_revision_not_found');
      const index = this.createIndex(revision, false);
      return this.ok(route, this.indexProjection(index), 202);
    }
    const graphMatch = path.match(/^\/indexes\/([^/]+)\/graph$/);
    if (method === 'GET' && graphMatch) {
      const index = this.indexes.get(graphMatch[1]);
      if (!index) return this.error(route, 404, 'test_support_index_not_found');
      if (index.status !== 'ready') return this.error(route, 409, 'test_support_index_not_ready');
      return this.ok(route, {
        index_id: index.index_id,
        source_revision_id: index.source_revision_id,
        nodes: [
          { id: 'node-entry', label: 'entry', kind: 'function' },
          { id: 'node-worker', label: 'worker', kind: 'class' },
        ],
        edges: [{ source: 'node-entry', target: 'node-worker', kind: 'calls' }],
      });
    }
    const reindexMatch = path.match(/^\/indexes\/([^/]+)\/reindex$/);
    if (method === 'POST' && reindexMatch) {
      const previous = this.indexes.get(reindexMatch[1]);
      if (!previous) return this.error(route, 404, 'test_support_index_not_found');
      const connection = this.connections.get(previous.connection_id);
      const revisionId = connection?.revisionIds.at(-1);
      const revision = revisionId ? this.revisions.get(revisionId) : undefined;
      if (!revision) return this.error(route, 409, 'test_support_latest_revision_missing');
      const replacement = this.createIndex(revision, true);
      return this.ok(route, {
        previous_index_id: previous.index_id,
        index: this.indexProjection(replacement),
        active_index_id: replacement.index_id,
      });
    }
    const indexMatch = path.match(/^\/indexes\/([^/]+)$/);
    if (method === 'GET' && indexMatch) {
      const index = this.indexes.get(indexMatch[1]);
      if (!index) return this.error(route, 404, 'test_support_index_not_found');
      this.advanceIndex(index);
      return this.ok(route, this.indexProjection(index));
    }
    const refreshMatch = path.match(/^\/connections\/([^/]+)\/refresh$/);
    if (method === 'POST' && refreshMatch) {
      const connection = this.connections.get(refreshMatch[1]);
      if (!connection) return this.error(route, 404, 'test_support_connection_not_found');
      const connector = String(connection.connection['connector_type']);
      const token = connector === 'github'
        ? this.githubRepository.next_commit
        : `workspace-rev-${connection.revisionIds.length + 1}`;
      const revision = this.createRevision(connection.connection, token);
      connection.revisionIds.push(String(revision['source_revision_id']));
      for (const index of this.indexes.values()) {
        if (index.connection_id === refreshMatch[1] && index.status === 'ready') {
          index.status = 'stale';
        }
      }
      return this.ok(route, {
        connection_id: refreshMatch[1],
        revision,
        previous_active_index_id: this.activeIndexByConnection.get(refreshMatch[1]) || null,
        active_index_state: 'stale',
      });
    }
    const indexStateMatch = path.match(/^\/connections\/([^/]+)\/index-state$/);
    if (method === 'GET' && indexStateMatch) {
      const connection = this.connections.get(indexStateMatch[1]);
      if (!connection) return this.error(route, 404, 'test_support_connection_not_found');
      return this.ok(route, {
        connection_id: indexStateMatch[1],
        active_index_id: this.activeIndexByConnection.get(indexStateMatch[1]) || null,
        indexes: [...this.indexes.values()]
          .filter((index) => index.connection_id === indexStateMatch[1])
          .map((index) => this.indexProjection(index)),
      });
    }
    const rollbackMatch = path.match(/^\/connections\/([^/]+)\/indexes\/rollback$/);
    if (method === 'POST' && rollbackMatch) {
      const target = this.indexes.get(String(body['target_index_id'] || ''));
      if (!target || target.connection_id !== rollbackMatch[1]) {
        return this.error(route, 422, 'test_support_rollback_target_invalid');
      }
      const previousActiveIndexId = this.activeIndexByConnection.get(rollbackMatch[1]) || null;
      target.status = 'ready';
      target.progress_percent = 100;
      this.activeIndexByConnection.set(rollbackMatch[1], target.index_id);
      const event = {
        audit_id: this.id('audit'),
        event_type: 'source_index_rollback',
        connection_id: rollbackMatch[1],
        previous_active_index_id: previousActiveIndexId,
        active_index_id: target.index_id,
        source_revision_id: target.source_revision_id,
        occurred_at: FIXED_TIME,
      };
      this.auditEvents.push(event);
      return this.ok(route, {
        connection_id: rollbackMatch[1],
        previous_active_index_id: previousActiveIndexId,
        active_index_id: target.index_id,
        audit_id: event.audit_id,
      });
    }
    if (method === 'GET' && path === '/destinations') {
      return this.ok(route, {
        items: this.destinations.map((destination) => destination.descriptor),
        count: this.destinations.length,
      });
    }
    if (method === 'GET' && path === '/governance/presets') {
      return this.ok(route, {
        items: this.destinations.map((destination) => ({
          preset_ref: destination.presetRef,
          destination_id: destination.descriptor['destination_id'],
          approval_required: destination.approvalRequired,
        })),
        count: this.destinations.length,
      });
    }
    if (method === 'POST' && path === '/governance/preview') {
      const revision = this.revisions.get(String(body['source_revision_id'] || ''));
      const destination = this.destinationById(String(body['destination_id'] || ''));
      if (!revision || !destination || body['model_id'] !== destination.descriptor['model_id']) {
        return this.error(route, 422, 'test_support_preview_binding_invalid');
      }
      if (
        destination.approvalRequired
        && body['preset_ref'] !== destination.presetRef
      ) return this.error(route, 422, 'test_support_preset_binding_invalid');
      if (destination.governanceProfile === 'local_allow') {
        return this.ok(route, this.preview(destination.descriptor, revision, 'allow', 'local_destination_allowed'));
      }
      if (destination.governanceProfile === 'runtime_deny') {
        return this.ok(route, this.preview(destination.descriptor, revision, 'deny', 'runtime_not_allowed'));
      }
      return this.ok(route, this.preview(destination.descriptor, revision, 'approval_required', 'approval_required'));
    }
    if (method === 'POST' && path === '/approvals') {
      const revision = this.revisions.get(String(body['source_revision_id'] || ''));
      const destination = this.destinationById(String(body['destination_id'] || ''));
      if (
        !revision
        || !destination
        || destination.governanceProfile !== 'single_use_approval'
        || body['model_id'] !== destination.descriptor['model_id']
        || body['preset_ref'] !== destination.presetRef
      ) return this.error(route, 422, 'test_support_approval_binding_invalid');
      const approval: ApprovalState = {
        approval_id: this.id('approval'),
        source_revision_id: String(body['source_revision_id']),
        destination_id: String(body['destination_id']),
        model_id: String(body['model_id']),
        consumed: false,
      };
      this.approvals.set(approval.approval_id, approval);
      return this.ok(route, {
        ...approval,
        scope: 'single_use',
        state: 'active',
      }, 201);
    }
    if (method === 'POST' && path === '/grants') {
      const revisionId = String(body['source_revision_id'] || '');
      const destinationId = String(body['destination_id'] || '');
      const modelId = String(body['model_id'] || '');
      const destination = this.destinationById(destinationId);
      if (!this.revisions.has(revisionId) || !destination) {
        return this.error(route, 422, 'test_support_grant_binding_invalid');
      }
      if (destination.governanceProfile === 'runtime_deny') {
        return this.error(route, 403, 'runtime_not_allowed');
      }
      if (destination.governanceProfile === 'single_use_approval') {
        const approval = this.approvals.get(String(body['approval_id'] || ''));
        if (!approval) return this.error(route, 403, 'approval_required');
        if (
          approval.source_revision_id !== revisionId
          || approval.destination_id !== destinationId
          || approval.model_id !== modelId
        ) return this.error(route, 403, 'approval_binding_mismatch');
        if (approval.consumed) return this.error(route, 409, 'approval_consumed');
        approval.consumed = true;
      } else if (modelId !== destination.descriptor['model_id']) {
        return this.error(route, 422, 'test_support_grant_model_invalid');
      }
      return this.ok(route, {
        grant: this.grant(revisionId, destinationId),
        decision: {
          decision: 'allow',
          reason_code: destination.governanceProfile === 'local_allow'
            ? 'local_destination_allowed'
            : 'approval_consumed_for_grant',
        },
      }, 201);
    }
    if (method === 'GET' && path === '/audit') {
      const connectionId = url.searchParams.get('connection_id');
      return this.ok(route, {
        items: this.auditEvents.filter((event) => event['connection_id'] === connectionId),
        count: this.auditEvents.filter((event) => event['connection_id'] === connectionId).length,
      });
    }
    if (method === 'POST' && path === '/retrieve') {
      const connectionId = String(body['connection_id'] || '');
      const activeIndexId = this.activeIndexByConnection.get(connectionId);
      const index = activeIndexId ? this.indexes.get(activeIndexId) : undefined;
      if (!index) return this.error(route, 409, 'test_support_active_index_missing');
      return this.ok(route, {
        connection_id: connectionId,
        active_index_id: index.index_id,
        source_revision_id: index.source_revision_id,
        chunks: [{
          chunk_id: this.id('chunk'),
          source_revision_id: index.source_revision_id,
          text: 'deterministic test-support retrieval',
        }],
      });
    }
    return this.error(route, 404, 'test_support_route_not_found');
  }

  private createSourcePair(connectorType: string, displayName: string, revisionToken: string) {
    const connectionId = this.id('conn');
    const digest = this.digest();
    const connection: JsonRecord = {
      schema: 'ananta.source-control.source-connection.v1',
      authority: 'hub',
      connection_id: connectionId,
      tenant_id: 'tenant-test-support',
      project_id: 'project-test-support',
      owner_id: 'owner-test-support',
      connector_type: connectorType,
      connection_identity_digest: digest,
      display_name: displayName,
      sensitivity: 'internal',
      state: 'active',
      created_at: FIXED_TIME,
    };
    const revision = this.createRevision(connection, revisionToken);
    this.connections.set(connectionId, {
      connection,
      revisionIds: [String(revision['source_revision_id'])],
    });
    return { connection, revision };
  }

  private createRevision(connection: JsonRecord, revisionToken: string): JsonRecord {
    const digest = this.digest();
    const revision: JsonRecord = {
      schema: 'ananta.source-control.source-revision.v1',
      authority: 'hub',
      source_revision_id: this.id('srev'),
      connection_id: connection['connection_id'],
      tenant_id: connection['tenant_id'],
      project_id: connection['project_id'],
      owner_id: connection['owner_id'],
      connector_type: connection['connector_type'],
      sensitivity: connection['sensitivity'],
      revision_token: revisionToken,
      revision_digest: digest,
      content_manifest_id: this.id('manifest'),
      content_manifest_digest: this.digest(),
      admission_state: 'admitted',
      captured_at: FIXED_TIME,
    };
    this.revisions.set(String(revision['source_revision_id']), revision);
    return revision;
  }

  private createIndex(revision: JsonRecord, ready: boolean): IndexState {
    const index: IndexState = {
      index_id: this.id('kidx'),
      connection_id: String(revision['connection_id']),
      source_revision_id: String(revision['source_revision_id']),
      status: ready ? 'ready' : 'queued',
      progress_percent: ready ? 100 : 0,
      reads: 0,
    };
    this.indexes.set(index.index_id, index);
    if (ready) this.activeIndexByConnection.set(index.connection_id, index.index_id);
    return index;
  }

  private advanceIndex(index: IndexState): void {
    if (index.status === 'ready' || index.status === 'stale') return;
    index.reads += 1;
    if (index.reads === 1) {
      index.status = 'indexing';
      index.progress_percent = 55;
      return;
    }
    index.status = 'ready';
    index.progress_percent = 100;
    this.activeIndexByConnection.set(index.connection_id, index.index_id);
  }

  private indexProjection(index: IndexState): JsonRecord {
    return {
      index_id: index.index_id,
      connection_id: index.connection_id,
      source_revision_id: index.source_revision_id,
      status: index.status,
      progress_percent: index.progress_percent,
      active: this.activeIndexByConnection.get(index.connection_id) === index.index_id,
    };
  }

  private destination(fields: JsonRecord): DestinationState {
    const {
      governance_profile: governanceProfile,
      approval_required: approvalRequired,
      preset_ref: presetRef,
      ...descriptorFields
    } = fields;
    return Object.freeze({
      descriptor: Object.freeze({
        schema: 'ananta.source-control.destination-descriptor.v1',
        authority: 'hub',
        destination_id: this.id('dst'),
        ...descriptorFields,
      }),
      governanceProfile: String(governanceProfile) as DestinationState['governanceProfile'],
      approvalRequired: approvalRequired === true,
      presetRef: String(presetRef),
    });
  }

  private destinationById(destinationId: string): DestinationState | undefined {
    return this.destinations.find(
      (destination) => destination.descriptor['destination_id'] === destinationId,
    );
  }

  private preview(
    destination: JsonRecord,
    revision: JsonRecord,
    decision: string,
    reasonCode: string,
  ): JsonRecord {
    return {
      source_revision_id: revision['source_revision_id'],
      destination_id: destination['destination_id'],
      worker_id: destination['worker_id'],
      worker_kind: destination['worker_kind'],
      runtime_id: destination['runtime_id'],
      runtime_kind: destination['runtime_kind'],
      provider_id: destination['provider_id'],
      provider_location: destination['provider_location'],
      model_id: destination['model_id'],
      model_class: destination['model_class'],
      operation: 'analyze',
      transformation: decision === 'allow' ? 'raw' : 'redacted',
      decision,
      reason_code: reasonCode,
      policy_version: 'policy-test-support:1',
    };
  }

  private grant(sourceRevisionId: string, destinationId: string): JsonRecord {
    return {
      schema: 'ananta.source-control.source-access-grant.v1',
      authority: 'hub',
      grant_id: this.id('grant'),
      version: 1,
      tenant_id: 'tenant-test-support',
      project_id: 'project-test-support',
      source_revision_id: sourceRevisionId,
      destination_id: destinationId,
      operation: 'analyze',
      transformation: 'raw',
      purpose: 'test_support.vertical_contract',
      policy_version: 'policy-test-support:1',
      state: 'active',
      issued_at: FIXED_TIME,
      expires_at: FIXED_EXPIRY,
    };
  }

  private id(prefix: string): string {
    const suffix = this.ordinal.toString(16).padStart(64, '0');
    this.ordinal += 1;
    return `${prefix}_${suffix}`;
  }

  private digest(): string {
    const digest = this.ordinal.toString(16).padStart(64, '0');
    this.ordinal += 1;
    return digest;
  }

  private body(route: Route): JsonRecord {
    try {
      const value = route.request().postDataJSON();
      return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
    } catch {
      return {};
    }
  }

  private async ok(route: Route, data: unknown, status = 200): Promise<void> {
    await this.respond(route, status, { status: 'success', data });
  }

  private async error(route: Route, status: number, reasonCode: string): Promise<void> {
    await this.respond(route, status, {
      status: 'error',
      reason_code: reasonCode,
      message: reasonCode,
    });
  }

  private async respond(route: Route, status: number, body: JsonRecord): Promise<void> {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify({
        ...body,
        test_support: {
          contract: SOURCE_CONTROL_TEST_SUPPORT_CONTRACT,
          deterministic: true,
          production_capability: false,
        },
      }),
    });
  }
}
