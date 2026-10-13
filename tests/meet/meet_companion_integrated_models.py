"""Synthetic MCP/profile composition over the real Hub SQL Evidence Registry."""

from unittest.mock import Mock

from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.repositories.meet_companion_tool_sources import SqlCompanionToolAttempts, SqlCompanionToolSources
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.meet_companion_model_inputs import MeetCompanionModelInputs
from agent.services.meet_companion_model_policy import HubCompanionModelPolicy
from agent.services.meet_companion_model_runs import MeetCompanionModelRuns
from agent.services.meet_companion_run_cleanup import MeetCompanionRunCleanup
from agent.services.meet_companion_task_tools import MeetCompanionTaskTools
from agent.services.meet_companion_tool_execution import HubCompanionToolExecution, HubCompanionToolPolicy
from agent.services.operation_policy_service import get_operation_policy_service
from agent.services.operation_registry_service import get_operation_registry_service


def configure_models(app, authority, grants, database, counts, *, actual=False):
    from agent.repositories.meet_companion_run_cleanup import SqlCompanionRunCleanup

    app.config['MEET_COMPANION_ASSIGNED_MODEL_PROFILE'] = {
        'backend': 'openai', 'model': 'qwen2.5:1.5b' if actual else 'synthetic-model',
        'environment_digest': 'e' * 64, 'auto_execute': True}
    resolver = Mock()
    resolver.resolve.side_effect = lambda **_kwargs: {
        'workspace_id': 'workspace', 'repository_id': 'repo', 'source_scope': 'repository',
        'revision': '1' * 40, 'allowed_paths': ['worker/'], 'allowed_index_ids': ['index'],
        'allowed_signals': ['exact']}
    app.extensions['codecompass_retrieval_capability_resolver'] = resolver
    backend = Mock()

    def execute(**kwargs):
        assert kwargs['context']['codecompass_capability']['allowed_paths'] == ['worker/']
        counts['tool'] += 1
        return {'content': [{'type': 'json', 'json': {'status': 'ok', 'answer': 'synthetic scoped repository'}}]}

    backend.call_tool.side_effect = execute
    sources = SqlCompanionToolSources(database)
    sources.initialize()
    attempts = SqlCompanionToolAttempts(database)
    attempts.initialize()
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))
    policy = HubCompanionToolPolicy(app, get_operation_registry_service(), get_operation_policy_service())
    tools = MeetCompanionTaskTools(authority, policy, HubCompanionToolExecution(app, backend), sources,
                                  registry, attempts=attempts, synthetic=True)
    model_sources = SqlCompanionToolSources(database, maximum=128_000)
    model_sources.initialize()
    models = MeetCompanionModelRuns(authority, HubCompanionModelPolicy(app),
        MeetCompanionModelInputs(registry, sources, policy), model_sources, registry,
        attempts=attempts, synthetic=True)
    app.extensions.update(meet_companion_task_tools=tools, meet_companion_model_runs=models)
    grants.model_policy = models.policy
    from agent.services.meet_companion_run_reconciliation import MeetCompanionRunReconciliation

    repository = SqlCompanionRunCleanup(database)
    authority.tasks.run_cleanup = MeetCompanionRunCleanup(repository, registry)
    app.extensions['meet_companion_run_reconciliation'] = MeetCompanionRunReconciliation(
        repository, authority.tasks.run_cleanup)


def wait_model_started(container, counts, *, assigned):
    import time

    from tests.meet_dialog_browser_fixture import docker

    deadline = time.monotonic() + 10
    while True:
        started = counts['provider'] > 0 if assigned else docker('exec', container.name, 'python', '-S', '-c',
            "from pathlib import Path;print(Path('/state/synthetic-model-started').exists())") == 'True'
        if started:
            return
        assert time.monotonic() < deadline, 'bounded model start missing'
        time.sleep(.1)


def wait_source_pause_checkpoint(container, scenario, receive):
    import time

    from tests.meet_dialog_browser_fixture import docker

    if scenario.endswith('-mid-speech'):
        assert receive(180) == {'speech_started': True}
    elif scenario == 'speech-preparation-paused':
        deadline = time.monotonic() + 80
        while docker('exec', container.name, 'python', '-S', '-c',
            "from pathlib import Path;print(Path('/state/synthetic-tts-started').exists())") != 'True':
            assert time.monotonic() < deadline, 'bounded actual assigned TTS start missing'
            time.sleep(.1)


def recover_crashed_worker(app, database, task_id, container, counts, receive):
    import time

    from sqlalchemy import select

    from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
    from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
    from agent.services.meet_dialog_deadlines import MeetDialogDeadlines
    from agent.services.task_runtime_service import compare_and_set_local_task_status
    from tests.meet_dialog_browser_fixture import docker

    docker('kill', container.name)
    assert docker('inspect', container.name, '--format', '{{.State.Running}}') == 'false'
    assert receive(30) == {'revoked': True, 'replies': 0, 'camera_absent': True}
    deadlines = MeetDialogDeadlines(SqlDialogDeadlines(database, task_status_cas=compare_and_set_local_task_status))
    deadline = time.monotonic() + 100
    while True:
        with app.app_context():
            deadlines.run_once(limit=25)
            app.extensions['meet_companion_run_reconciliation'].run_once(limit=25)
            task = app.extensions['meet_dialog_service'].tasks.get_by_id(task_id)
            with database.connect() as connection:
                runs = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
                    HubRunEvidenceIdentityDB.task_id == task_id)).mappings().all()
        if task.status == 'failed' and all(run['state'] != 'reserved' for run in runs):
            break
        assert task.status == 'in_progress', task.status
        assert time.monotonic() < deadline, 'bounded crashed Worker deadline recovery missing'
        time.sleep(.2)
    assert len(runs) == 2 and sorted(run['state'] for run in runs) == ['failed', 'succeeded']
    assert all(run['synthetic'] and run['evidence_scope'] == 'test' for run in runs)
    assert counts['tool'] == counts['provider'] == 1 and counts['finish'] == counts['legacy'] == 0


def register_provider(app, database, counts, *, enabled, actual_state=None):
    if not enabled:
        return
    import time

    from flask import jsonify, request
    from sqlalchemy import select

    from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB

    @app.get('/synthetic-model/models')
    def model_listing():
        return jsonify({'data': [{'id': 'qwen2.5:1.5b' if actual_state is not None else 'synthetic-model'}]})

    @app.post('/synthetic-model/chat/completions')
    def model_provider():
        with database.connect() as connection:
            active = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
                HubRunEvidenceIdentityDB.state == 'reserved')).mappings().one()
        assert (len(active['source_ids']) >= 2 if actual_state is not None
                else len(active['source_ids']) == 2) and active['synthetic']
        assert any(row.get('role') == 'tool' and '[untrusted Hub tool codecompass.retrieve; source SRC_'
                   in row.get('content', '') for row in request.get_json()['messages'])
        counts['provider'] += 1
        number = counts['provider']
        time.sleep(50 if number == 1 and actual_state is None else .1)
        if actual_state is not None:
            return jsonify(actual_state['provider'].request('/v1/chat/completions', request.get_json()))
        return jsonify({'model': 'synthetic-model', 'choices': [{'message': {
            'role': 'assistant', 'content': 'Synthetic companion answer ' + str(number)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})


def assert_model_runs(database, task_id, counts, cancelled, *, enabled, actual=False):
    if not enabled:
        return
    from sqlalchemy import select

    from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB

    expected = 1 if cancelled else 3
    if actual:
        assert not cancelled and counts['tool'] >= expected and counts['provider'] >= expected
    else:
        assert counts['tool'] == counts['provider'] == expected
    with database.connect() as connection:
        runs = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
            HubRunEvidenceIdentityDB.task_id == task_id)).mappings().all()
    assert len(runs) == (counts['tool'] + counts['provider'] if actual else expected * 2)
    assert all(row['synthetic'] and row['evidence_scope'] == 'test' for row in runs)
    assert sorted(row['state'] for row in runs) == (
        ['cancelled', 'succeeded'] if cancelled else ['succeeded'] * len(runs))
