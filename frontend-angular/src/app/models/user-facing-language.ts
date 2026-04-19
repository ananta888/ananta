export type PlatformTerm =
  | 'artifact'
  | 'blueprint'
  | 'verification'
  | 'exposure-policy'
  | 'federation'
  | 'routing'
  | 'tool-approval'
  | 'blocked';

export interface UserFacingTerm {
  term: PlatformTerm;
  label: string;
  technicalLabel: string;
  hint: string;
}

export const USER_FACING_TERMS: Record<PlatformTerm, UserFacingTerm> = {
  artifact: {
    term: 'artifact',
    label: 'Ergebnis',
    technicalLabel: 'Artifact',
    hint: 'Eine Datei, Zusammenfassung oder ein anderes Resultat, das aus einer Aufgabe entstanden ist.',
  },
  blueprint: {
    term: 'blueprint',
    label: 'Vorlage',
    technicalLabel: 'Blueprint',
    hint: 'Ein wiederverwendbarer Ablauf fuer aehnliche Ziele.',
  },
  verification: {
    term: 'verification',
    label: 'Pruefung',
    technicalLabel: 'Verification',
    hint: 'Ein Sicherheits- und Qualitaetscheck, bevor ein Ergebnis als belastbar gilt.',
  },
  'exposure-policy': {
    term: 'exposure-policy',
    label: 'Freigabe-Regel',
    technicalLabel: 'Exposure Policy',
    hint: 'Legt fest, welche Daten oder Aktionen sichtbar bzw. erlaubt sind.',
  },
  federation: {
    term: 'federation',
    label: 'Verbund',
    technicalLabel: 'Federation',
    hint: 'Mehrere Ananta-Instanzen oder Dienste arbeiten kontrolliert zusammen.',
  },
  routing: {
    term: 'routing',
    label: 'Zuweisung',
    technicalLabel: 'Routing',
    hint: 'Ananta waehlt aus, welche Ausfuehrungsumgebung fuer diese Aufgabe passt.',
  },
  'tool-approval': {
    term: 'tool-approval',
    label: 'Tool-Freigabe',
    technicalLabel: 'Tool Approval',
    hint: 'Bestimmte Werkzeuge werden nur genutzt, wenn sie fuer diese Aufgabe erlaubt sind.',
  },
  blocked: {
    term: 'blocked',
    label: 'Wartet auf Klaerung',
    technicalLabel: 'Blocked',
    hint: 'Ananta haelt an, bis eine fehlende Information, Freigabe oder Pruefung vorliegt.',
  },
};

export function userFacingTerm(term: PlatformTerm): UserFacingTerm {
  return USER_FACING_TERMS[term];
}

export function userFacingTermLabel(term: PlatformTerm): string {
  return userFacingTerm(term).label;
}

export function userFacingTermHint(term: PlatformTerm): string {
  return userFacingTerm(term).hint;
}

export function decisionExplanation(kind: PlatformTerm): string {
  switch (kind) {
    case 'routing':
      return 'Ananta weist Arbeit gezielt zu. So bleiben Planung, Ausfuehrung und Pruefung getrennt und nachvollziehbar.';
    case 'verification':
      return 'Ananta prueft Ergebnisse, bevor du sie weiterverwendest oder als abgeschlossen bewertest.';
    case 'tool-approval':
      return 'Tool-Nutzung bleibt begrenzt, damit Aktionen nachvollziehbar und freigegeben bleiben.';
    case 'blocked':
      return 'Die Ausfuehrung pausiert, weil noch eine Information, Freigabe oder Pruefung fehlt.';
    default:
      return userFacingTermHint(kind);
  }
}

export function safetyBoundaryExplanation(status?: string | null): string {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'blocked') {
    return 'Diese Aufgabe wartet bewusst. Pruefe Kontext, Review oder fehlende Eingaben, bevor sie weiterlaeuft.';
  }
  if (normalized === 'failed') {
    return 'Die Ausfuehrung wurde gestoppt. Oeffne Logs und Ergebnisdetails, bevor du erneut startest.';
  }
  if (normalized === 'review_required' || normalized === 'pending_review') {
    return 'Eine manuelle Freigabe ist erforderlich, bevor Ananta den Vorschlag ausfuehrt.';
  }
  return 'Sicherheits- und Pruefhinweise erscheinen hier, sobald Ananta eine Grenze erkennt.';
}
