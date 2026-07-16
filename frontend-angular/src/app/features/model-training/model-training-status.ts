import { StatusTone } from '../../shared/ui/state';

const SUCCESS = new Set(['valid', 'completed', 'approved', 'active', 'evaluated']);
const WARNING = new Set(['queued', 'claimed', 'validating', 'cancel_requested', 'imported_pending_evaluation', 'deprecated']);
const ERROR = new Set(['invalid', 'failed', 'interrupted', 'rejected']);
const ACTIVE = new Set(['running']);
const PAUSED = new Set(['cancelled']);

const HTTP_RECOVERY: Record<number, string> = {
  401: 'Sitzung abgelaufen oder ungültig. Bitte neu anmelden und den Vorgang erneut starten.',
  403: 'Diese Aktion benötigt Administratorrechte. Berechtigung prüfen oder mit einem Admin-Konto neu anmelden.',
  409: 'Der Stand wurde zwischenzeitlich geändert. Daten aktualisieren, Ergebnis prüfen und die Aktion erneut ausführen.',
  413: 'Der Upload überschreitet das Hub-Größenlimit. Eine kleinere bzw. bereinigte Datei wählen und erneut versuchen.',
  422: 'Der Hub hat Inhalt oder Konfiguration fachlich abgelehnt. Markierte Daten korrigieren, erneut validieren und dann wiederholen.',
  503: 'Der Training-Control-Service ist vorübergehend nicht verfügbar. Hub-Erreichbarkeit prüfen und später erneut versuchen.',
};

const REASON_RECOVERY: Record<string, string> = {
  dataset_referenced: 'Das Dataset wird von einem Job oder einem anderen Dataset referenziert und kann nicht gelöscht werden. Referenzen zuerst regulär auflösen; ein Force-Delete wird nicht angeboten.',
};

export function trainingStatusTone(status: string | null | undefined): StatusTone {
  const normalized = String(status || '').trim().toLowerCase();
  if (SUCCESS.has(normalized)) return 'success';
  if (WARNING.has(normalized)) return 'warning';
  if (ERROR.has(normalized)) return 'error';
  if (ACTIVE.has(normalized)) return 'active';
  if (PAUSED.has(normalized)) return 'paused';
  return normalized ? 'info' : 'unknown';
}

export function shortHash(value: string | null | undefined): string {
  const normalized = String(value || '').replace(/^sha256:/, '');
  return normalized ? normalized.slice(0, 12) : '-';
}

export function boundedText(value: unknown, maxLength = 500): string {
  let text = String(value ?? '');
  text = text
    .replace(/(bearer\s+)[a-z0-9._~+/=-]+/gi, '$1[REDACTED]')
    .replace(/("(?:api[_-]?key|token|secret|password)"\s*:\s*)"(?:\\.|[^"])*"/gi, '$1"[REDACTED]"')
    .replace(/((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+/gi, '$1[REDACTED]');
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

export function boundedTrainingLog(value: unknown, maxLength = 700): string {
  const redacted = boundedText(value, maxLength * 2)
    .replace(/("(?:instruction|input|output|messages|expected_output|training_record)"\s*:\s*)("(?:\\.|[^"])*"|\[[^\]]*\]|\{[^}]*\})/gi, '$1"[TRAINING_DATA_REDACTED]"')
    .replace(/((?:instruction|input|output|messages|expected[_ -]?output|training[_ -]?record|prompt)\s*[:=]\s*)[^\r\n]*/gi, '$1[TRAINING_DATA_REDACTED]');
  return redacted.length > maxLength ? `${redacted.slice(0, maxLength)}…` : redacted;
}

export function idempotencyKey(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${id}`;
}

export function apiErrorMessage(error: any, fallback: string): string {
  let current = error?.error ?? error;
  for (let index = 0; index < 4; index += 1) {
    if (current && typeof current === 'object' && 'data' in current) current = current.data;
    else if (current && typeof current === 'object' && current.error && typeof current.error === 'object') current = current.error;
    else break;
  }
  const code = current?.reason_code || current?.code;
  const message = current?.message || current?.error || error?.message;
  const detail = code && message && code !== message ? `${code}: ${message}` : String(message || code || fallback);
  const recovery = REASON_RECOVERY[String(code || '')] || HTTP_RECOVERY[Number(error?.status || current?.status || 0)];
  return boundedText(recovery ? `${detail} ${recovery}` : detail, 900);
}
