"""Hub admission independently checks required Worker analysis reports."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent.services.self_development.analysis_verification import (
    AnalysisVerificationError, verify_worker_analysis,
)
from agent.services.self_development.runtime import apply_worker_scope


def _report(tmp_path):
    source_hash = hashlib.sha256(b'value = 1\n').hexdigest()
    reference = {'path': 'agent/value.py', 'file_sha256': source_hash,
                 'line_start': 1, 'line_end': 1}
    report = {'schema': 'ananta_worker_mutation_report.v1', 'task_id': 'assigned-task',
        'outcome': 'final_answer', 'code_analysis': {'required': True, 'status': 'accepted',
            'codecompass_observed': True, 'observed_sources': [reference], 'analysis': {
                'sources': [reference], 'findings': 'Constant value.',
                'change_reasoning': 'Read configured value.', 'test_reasoning': 'Compare configurations.'}}}
    (tmp_path / '.ananta').mkdir()
    return report, {'agent/value.py': source_hash}


def test_hub_accepts_analysis_of_original_source_after_legitimate_mutation(tmp_path):
    report, baseline = _report(tmp_path)
    (tmp_path / 'agent').mkdir()
    (tmp_path / 'agent/value.py').write_text('value = 2\n')
    (tmp_path / '.ananta/mutation-report.json').write_text(json.dumps(report))

    accepted = verify_worker_analysis(tmp_path, task_id='assigned-task', baseline=baseline)

    assert accepted.code_analysis.analysis.sources[0].file_sha256 == baseline['agent/value.py']


@pytest.mark.parametrize('change', ['task', 'source', 'unobserved', 'not_required', 'no_codecompass'])
def test_hub_rejects_analysis_missing_assignment_or_source_binding(tmp_path, change):
    report, baseline = _report(tmp_path)
    if change == 'task':
        report['task_id'] = 'other-task'
    if change == 'source':
        baseline['agent/value.py'] = hashlib.sha256(b'other revision').hexdigest()
    if change == 'unobserved':
        report['code_analysis']['observed_sources'] = []
    if change == 'not_required':
        report['code_analysis']['required'] = False
    if change == 'no_codecompass':
        report['code_analysis']['codecompass_observed'] = False
    (tmp_path / '.ananta/mutation-report.json').write_text(json.dumps(report))

    with pytest.raises(AnalysisVerificationError):
        verify_worker_analysis(tmp_path, task_id='assigned-task', baseline=baseline)


def test_hub_rejects_missing_analysis_report(tmp_path):
    with pytest.raises(AnalysisVerificationError, match='report_required'):
        verify_worker_analysis(tmp_path, task_id='assigned-task', baseline={})


def test_explicit_contract_policy_propagates_analysis_without_planning(monkeypatch):
    saved = []
    policy = SimpleNamespace(planning_enabled=lambda: False, value={
        'allowed_roots': ['tests'], 'quality_gates': {'require_code_analysis': True},
        'test_profiles': {'unit': {'command': 'pytest tests'}}})
    app = SimpleNamespace(config={'AGENT_CONFIG': {}},
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a, **kw: None))
    monkeypatch.setattr('agent.services.repository_registry.get_repository_registry',
        lambda: SimpleNamespace(config_repo=SimpleNamespace(save=saved.append)))

    assert apply_worker_scope(app, policy) is True

    assert app.config['AGENT_CONFIG']['ananta_worker_workspace_mutation']['require_code_analysis'] is True
    assert json.loads(saved[0].value_json)['require_code_analysis'] is True
    assert apply_worker_scope(app, policy) is False
