import type { VpRuntimeOverlay } from '../../visual-process/visual-process-api.service';
import type { CaseFlowAgentCanvasNodeProjection } from './caseflow-agent-canvas.models';

export type CaseFlowAgentRuntimeStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'success'
  | 'error'
  | 'skipped'
  | 'cancelled'
  | 'unknown';

export interface CaseFlowAgentRuntimeNodeProjection {
  readonly step_id: string;
  readonly status: CaseFlowAgentRuntimeStatus;
  readonly label: string;
  readonly icon: string;
  readonly current: boolean;
  readonly active: boolean;
}

export interface CaseFlowAgentRuntimeProjection {
  readonly available: boolean;
  readonly nodes: Readonly<Record<string, CaseFlowAgentRuntimeNodeProjection>>;
  readonly current_step_ids: readonly string[];
  /** Edge activity requires the authorized CAC-007A read model, never a guess. */
  readonly active_edge_ids: readonly [];
}

const STATUS_PRESENTATION: Readonly<
  Record<CaseFlowAgentRuntimeStatus, Readonly<{ label: string; icon: string }>>
> = {
  pending: { label: 'Ausstehend', icon: 'schedule' },
  running: { label: 'Läuft', icon: 'play_circle' },
  awaiting_approval: { label: 'Wartet auf Freigabe', icon: 'approval' },
  success: { label: 'Erfolgreich', icon: 'check_circle' },
  error: { label: 'Fehler', icon: 'error' },
  skipped: { label: 'Übersprungen', icon: 'skip_next' },
  cancelled: { label: 'Abgebrochen', icon: 'cancel' },
  unknown: { label: 'Unbekannt', icon: 'help_outline' },
};

/** Pure runtime projection. Graph and runtime inputs remain untouched. */
export function projectCaseFlowAgentRuntime(
  graphId: string,
  nodes: readonly CaseFlowAgentCanvasNodeProjection[],
  runtime: VpRuntimeOverlay | null | undefined,
): CaseFlowAgentRuntimeProjection {
  const available = isRuntimeBoundToGraph(graphId, runtime);
  const boundRuntime = available ? runtime : null;
  const currentIds = new Set(boundRuntime?.current_step_ids ?? []);
  const projected: Record<string, CaseFlowAgentRuntimeNodeProjection> = {};
  for (const node of nodes) {
    const status = normalizeRuntimeStatus(boundRuntime?.steps?.[node.step_id]?.status);
    const presentation = STATUS_PRESENTATION[status];
    const current = currentIds.has(node.step_id);
    projected[node.step_id] = {
      step_id: node.step_id,
      status,
      label: presentation.label,
      icon: presentation.icon,
      current,
      active: current && (status === 'running' || status === 'awaiting_approval'),
    };
  }
  return {
    available,
    nodes: projected,
    current_step_ids: [...currentIds].filter(stepId => Object.hasOwn(projected, stepId)),
    active_edge_ids: [],
  };
}

function isRuntimeBoundToGraph(
  graphId: string,
  runtime: VpRuntimeOverlay | null | undefined,
): runtime is VpRuntimeOverlay {
  if (!runtime) return false;
  const runtimeGraphId = runtime.process_id ?? runtime.workflow_id;
  if (runtimeGraphId !== graphId) return false;
  return Object.entries(runtime.steps).every(([stepId, step]) => step.step_id === stepId);
}

function normalizeRuntimeStatus(
  status: VpRuntimeOverlay['steps'][string]['status'] | undefined,
): CaseFlowAgentRuntimeStatus {
  switch (status) {
    case 'pending':
    case 'running':
    case 'awaiting_approval':
    case 'skipped':
    case 'cancelled':
      return status;
    case 'succeeded':
      return 'success';
    case 'failed':
      return 'error';
    default:
      return 'unknown';
  }
}
