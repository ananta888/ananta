export interface BlueprintRecommendationInput {
  goalType?: string;
  domain?: string;
  executionStyle?: string;
  strictness?: string;
  modeId?: string;
  modeTitle?: string;
}

export interface BlueprintRecommendationResult {
  blueprintName: string;
  reasons: string[];
  reviewNote: string;
}

function normalize(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function inferGoalTypeFromMode(modeId: string, modeTitle: string): string {
  const id = normalize(modeId);
  const title = normalize(modeTitle);
  const all = `${id} ${title}`;

  if (all.includes('research') || all.includes('analyse') || all.includes('analysis')) return 'research';
  if (all.includes('security') || all.includes('compliance')) return 'security_review';
  if (all.includes('release')) return 'release_prep';
  if (all.includes('repair') || all.includes('bug') || all.includes('incident') || all.includes('fix')) return 'bugfix';
  if (all.includes('new-project') || all.includes('new software') || all.includes('projekt anlegen')) return 'project_setup';
  if (all.includes('evolution') || all.includes('weiterentwickeln')) return 'new_feature';
  return '';
}

export function recommendBlueprint(input: BlueprintRecommendationInput): BlueprintRecommendationResult {
  const modeGoalType = inferGoalTypeFromMode(input.modeId || '', input.modeTitle || '');
  const goalType = normalize(input.goalType) || modeGoalType;
  const domain = normalize(input.domain);
  const executionStyle = normalize(input.executionStyle);
  const strictness = normalize(input.strictness);
  const reasons: string[] = [];

  if (goalType === 'security_review' || domain === 'security') {
    reasons.push('Security/Compliance steht im Vordergrund.');
    return {
      blueprintName: 'Security-Review',
      reasons,
      reviewNote: 'Reviewbar: Scope, Controls und Remediation lassen sich vor Team-Start klar pruefen.',
    };
  }

  if (goalType === 'release_prep' || domain === 'release') {
    reasons.push('Release-Readiness und Go/No-Go sind zentral.');
    return {
      blueprintName: 'Release-Prep',
      reasons,
      reviewNote: 'Reviewbar: Verifikation und Rollback-Readiness sind vor Instanziierung sichtbar.',
    };
  }

  if (goalType === 'research') {
    if (executionStyle === 'evolution') {
      reasons.push('Research soll direkt in einen evolvierbaren Vorschlag uebergehen.');
      return {
        blueprintName: 'Research-Evolution',
        reasons,
        reviewNote: 'Reviewbar: DeerFlow/Proposal und Review-Gate sind im Ablauf getrennt sichtbar.',
      };
    }
    reasons.push('Evidenzbasierte Analyse ist der Hauptmodus.');
    return {
      blueprintName: 'Research',
      reasons,
      reviewNote: 'Reviewbar: Quellenlage und Findings koennen vor Team-Start abgestimmt werden.',
    };
  }

  if (goalType === 'bugfix') {
    if (executionStyle === 'opencode') {
      reasons.push('Bugfix mit expliziter Ausfuehrungskaskade ist gewuenscht.');
      return {
        blueprintName: 'Scrum-OpenCode',
        reasons,
        reviewNote: 'Reviewbar: Ausfuehrungskaskade und Inkrement-Validierung sind vorab sichtbar.',
      };
    }
    reasons.push('Stoerung/Fix mit Regression-Absicherung ist gefragt.');
    return {
      blueprintName: 'Code-Repair',
      reasons,
      reviewNote: 'Reviewbar: Triage, Patch-Plan und Regression-Checks werden vor Team-Start vorbereitet.',
    };
  }

  if (executionStyle === 'flow') {
    reasons.push('Kontinuierlicher Arbeitsfluss mit WIP-Orientierung passt am besten.');
    return {
      blueprintName: 'Kanban',
      reasons,
      reviewNote: 'Reviewbar: WIP-Regeln und Flow-Checks bleiben vor Team-Start nachvollziehbar.',
    };
  }

  if (strictness === 'strict') {
    reasons.push('Erhoehte Striktheit bevorzugt kontrollierte Review-Profile.');
    return {
      blueprintName: 'Security-Review',
      reasons,
      reviewNote: 'Reviewbar: striktere Kontrollpunkte sind fuer den Start transparent.',
    };
  }

  reasons.push('Iterativer Team-Start mit klaren Rollen ist der solide Standard.');
  return {
    blueprintName: 'Scrum',
    reasons,
    reviewNote: 'Reviewbar: Backlog, Sprint-Plan und Reviewpunkte sind direkt nachvollziehbar.',
  };
}
