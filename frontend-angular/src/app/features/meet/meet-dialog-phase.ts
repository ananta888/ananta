/** Hub observation metadata, never authority to join or proof of decoded media. */
export interface MeetDialogPhase {
  schema: 'ananta.meet-dialog-phase.v1'; task_id: string;
  phase: 'queued' | 'admitted' | 'connecting' | 'joined' | 'publishing' | 'stopping' | 'completed' | 'failed' | 'cancelled';
  revision: number; task_status: 'in_progress' | 'completed' | 'failed' | 'cancelled';
  last_phase_at: number; observation_fresh: boolean; observed_at: number | null;
  publication_revision: number | null; registered_sources: ('camera' | 'microphone' | 'screen')[];
}
const terminal = ['completed', 'failed', 'cancelled'];
const phases = ['queued', 'admitted', 'connecting', 'joined', 'publishing', 'stopping', ...terminal];
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

export function validateDialogPhase(input: unknown, task: string): MeetDialogPhase {
  const value = input as MeetDialogPhase;
  if (!value || typeof value !== 'object'
    || Object.keys(value).sort().join() !== 'last_phase_at,observation_fresh,observed_at,phase,publication_revision,registered_sources,revision,schema,task_id,task_status'
    || value.schema !== 'ananta.meet-dialog-phase.v1' || value.task_id !== task
    || typeof task !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(task)
    || !phases.includes(value.phase) || !['in_progress', ...terminal].includes(value.task_status)
    || !integer(value.revision) || value.revision < 1 || !integer(value.last_phase_at) || value.last_phase_at > 8_640_000_000_000_000
    || typeof value.observation_fresh !== 'boolean' || !Array.isArray(value.registered_sources)
    || value.registered_sources.some(source => !['camera', 'microphone', 'screen'].includes(source))
    || value.registered_sources.join() !== [...new Set(value.registered_sources)].sort().join()
    || (value.task_status === 'in_progress' ? terminal.includes(value.phase) : value.phase !== value.task_status)
    || value.observation_fresh && (value.task_status !== 'in_progress' || !['joined', 'publishing'].includes(value.phase))) {
    throw new Error('meet_dialog_phase_contract_invalid');
  }
  const observed = value.observed_at !== null;
  if (observed ? !integer(value.observed_at) || value.observed_at > value.last_phase_at || !integer(value.publication_revision)
    : value.publication_revision !== null || value.registered_sources.length !== 0 || value.observation_fresh) {
    throw new Error('meet_dialog_phase_contract_invalid');
  }
  if (value.phase === 'publishing' && (!observed || value.registered_sources.length === 0)
    || value.phase === 'joined' && value.registered_sources.length !== 0
    || ['queued', 'admitted', 'connecting'].includes(value.phase) && observed
    || value.registered_sources.length > 0 && value.publication_revision === 0) {
    throw new Error('meet_dialog_phase_contract_invalid');
  }
  return value;
}
