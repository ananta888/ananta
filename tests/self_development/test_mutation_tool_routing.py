"""DPRV tool_routing inside the workspace-mutation loop's read phase."""
from types import SimpleNamespace

from agent.cli_backends.workspace_mutation import tool_actions, tool_routing


class _Service:
    def __init__(self, choice='codecompass.plan_context', confidence=0.95, mode='active', option=None):
        self._choice = choice
        self._confidence = confidence
        self._mode = mode
        self._option = option
        self.decisions = []

    def area_mode(self, area):
        return self._mode

    def area_option(self, area, key, default=None):
        return self._option if self._option is not None else default

    def decide(self, area, request, question_thresholds=None):
        self.decisions.append((area, request, question_thresholds))
        answer = SimpleNamespace(choice=self._choice, confidence=self._confidence)
        return SimpleNamespace(usable=True, outcome=SimpleNamespace(
            result=SimpleNamespace(answer=lambda key: answer)))


def test_is_blind_read_tool():
    assert tool_routing.is_blind_read_tool('repo.read_file_range')
    assert tool_routing.is_blind_read_tool('repo.grep')
    assert not tool_routing.is_blind_read_tool('codecompass.plan_context')
    assert not tool_routing.is_blind_read_tool('repo.apply_patch')


def test_route_read_tool_returns_confident_candidate():
    service = _Service('codecompass.plan_context', 0.9)
    routed = tool_routing.route_read_tool(query='fix auth', state='fix auth', config={'mode': 'active'},
                                          allowed_tools=['codecompass.search', 'codecompass.plan_context'],
                                          service=service)
    assert routed == {'tool_name': 'codecompass.plan_context', 'arguments': {'query': 'fix auth'}}
    assert service.decisions[0][0] == 'tool_routing'
    labels = {label for label, _ in service.decisions[0][1].questions[0].options}
    assert labels == {'codecompass.plan_context', tool_routing.KEEP_CURRENT}


def test_route_read_tool_ignores_answer_that_is_not_the_offered_tool():
    service = _Service('codecompass.search', 0.99)
    assert tool_routing.route_read_tool(query='q', state='q', config={'mode': 'active'},
                                        allowed_tools=['codecompass.plan_context'], service=service) is None


def test_route_read_tool_keeps_current_when_model_keeps_read():
    service = _Service(tool_routing.KEEP_CURRENT, 0.99)
    assert tool_routing.route_read_tool(query='q', state='q', allowed_tools=['codecompass.search'],
                                        config={'mode': 'active'}, service=service) is None


def test_route_read_tool_requires_confidence_threshold():
    service = _Service('codecompass.search', 0.4)
    assert tool_routing.route_read_tool(query='q', state='q', allowed_tools=['codecompass.search'],
                                        config={'mode': 'active'}, service=service) is None


def test_route_read_tool_disabled_area_or_no_candidates():
    assert tool_routing.route_read_tool(query='q', state='q', allowed_tools=['codecompass.search'],
                                        config={'mode': 'disabled'}, service=_Service()) is None
    assert tool_routing.route_read_tool(query='q', state='q', allowed_tools=['repo.read_file_range'],
                                        config={'mode': 'active'}, service=_Service()) is None
    assert tool_routing.route_read_tool(query='q', state='q', allowed_tools=['codecompass.search'],
                                        config={'mode': 'active'}, service=_Service(mode='off')) is None


def test_prefer_read_tool_upgrades_blind_read(monkeypatch):
    monkeypatch.setattr('agent.cli_backends.helpers._get_agent_config', lambda: {
        'ananta_worker_tool_loop': {'tiny_router': {'mode': 'active'},
                                    'allowed_tools': ['codecompass.plan_context', 'repo.read_file_range']}})
    service = _Service('codecompass.plan_context', 0.9)
    session = SimpleNamespace(mode='strict_patch_request', task_prompt='fix the auth guard')
    chosen, arguments = tool_routing.prefer_read_tool(
        session=session, tool_name='repo.read_file_range', arguments={'path': 'a.py'}, service=service)
    assert chosen == 'codecompass.plan_context'
    assert arguments == {'query': 'fix the auth guard'}
    assert 'fix the auth guard' in service.decisions[0][1].state


def test_prefer_read_tool_leaves_non_blind_and_writes(monkeypatch):
    monkeypatch.setattr('agent.cli_backends.helpers._get_agent_config', lambda: {
        'ananta_worker_tool_loop': {'tiny_router': {'mode': 'active'},
                                    'allowed_tools': ['codecompass.plan_context']}})
    session = SimpleNamespace(mode='strict_patch_request', task_prompt='x')
    service = _Service('codecompass.plan_context', 0.9)
    for tool in ('codecompass.plan_context', 'repo.apply_patch', 'repo.write_file'):
        chosen, arguments = tool_routing.prefer_read_tool(
            session=session, tool_name=tool, arguments={'a': 1}, service=service)
        assert chosen == tool


def test_is_read_only_tool_covers_reads_not_writes():
    assert tool_routing.is_read_only_tool('repo.read_file_range')
    assert tool_routing.is_read_only_tool('codecompass.plan_context')
    assert tool_routing.is_read_only_tool('workspace.diff')
    assert not tool_routing.is_read_only_tool('repo.apply_patch')
    assert not tool_routing.is_read_only_tool('test.run')


def test_build_iteration_prompt_renders_action_nudge():
    from agent.cli_backends.workspace_mutation.prompts import build_iteration_prompt

    plain = build_iteration_prompt(original_prompt='p', instructions='i', evidence_blocks=[],
                                   iteration=2, max_iterations=16, max_chars_per_block=1000)
    nudged = build_iteration_prompt(original_prompt='p', instructions='i', evidence_blocks=[],
                                    iteration=2, max_iterations=16, max_chars_per_block=1000,
                                    nudge=tool_routing.ACTION_NUDGE)
    assert 'Handlung erforderlich' not in plain
    assert 'Handlung erforderlich' in nudged
    assert 'patch_request' in nudged


def test_instructions_treat_declared_file_not_found_as_create():
    from agent.cli_backends.workspace_mutation.prompts import build_mode_instructions

    instructions = build_mode_instructions('controlled_workspace')
    assert 'file_not_found' in instructions
    assert 'NEU' in instructions or 'erst' in instructions.lower()
    assert 'workspace_write' in instructions


def test_instructions_require_test_run_before_final_answer():
    from agent.cli_backends.workspace_mutation.prompts import build_mode_instructions

    instructions = build_mode_instructions('controlled_workspace')
    assert 'VOR `final_answer`' in instructions
    assert 'test.run' in instructions


def test_orchestrator_iteration_prompt_forwards_nudge():
    from agent.cli_backends.workspace_mutation._orchestrator import _build_iteration_prompt

    out = _build_iteration_prompt(original_prompt='p', instructions='i', evidence_blocks=[],
                                  iteration=1, max_iterations=4, max_chars_per_block=500, nudge='ACT NOW')
    assert 'ACT NOW' in out


def _finish_session(**attrs):
    from agent.cli_backends.workspace_mutation.session import MutationSession

    class _Session:
        def __init__(self):
            self.__dict__.update(attrs)
            self.outcome = None

        def write_report(self, outcome):
            self.outcome = outcome

    return _Session(), MutationSession.finish


def test_finish_uses_the_command_the_loop_ran():
    session, finish = _finish_session(cfg={"return_executable_proposal": True,
                                           "allowlisted_test_commands": ["a", "b"]},
                                      verification_command="pytest -q tests/x")
    rc, out, err = finish(session, "final_answer", 0, "", "")
    assert rc == 0
    assert "pytest -q tests/x" in out


def test_finish_falls_back_to_single_allowlisted_command():
    session, finish = _finish_session(cfg={"return_executable_proposal": True,
                                           "allowlisted_test_commands": ["only-cmd"]},
                                      verification_command="")
    rc, out, err = finish(session, "final_answer", 0, "", "")
    assert rc == 0
    assert "only-cmd" in out


def test_finish_without_command_is_bounded(monkeypatch):
    session, finish = _finish_session(cfg={"return_executable_proposal": True,
                                           "allowlisted_test_commands": ["a", "b"]},
                                      verification_command="")
    rc, out, err = finish(session, "final_answer", 0, "", "")
    assert rc == 1
    assert "verification_command_required" in out


def test_handle_tool_request_blocks_reads_after_force_threshold(monkeypatch):
    monkeypatch.setattr('agent.cli_backends.workspace_mutation.tool_actions._route_read_tool',
                        lambda session, turn, tool_name, arguments: (tool_name, arguments))
    added = []
    session = SimpleNamespace(cfg={'force_action_after': 6}, read_only_streak=6, mode='strict_patch_request',
                              task_prompt='x', add_evidence=lambda entry: added.append(entry),
                              next_tool_call_id=lambda prefix: 'tc-1')
    turn = SimpleNamespace(row={}, message={'tool_name': 'repo.read_file_range', 'arguments': {}}, err='')
    assert tool_actions.handle_tool_request(session, turn) is None
    assert turn.row['tool_status'] == 'error'
    assert added and added[0]['error'] == 'read_only_blocked_action_required'
    assert 'action_required' in (added[0].get('data') or {})
    assert 'patch_request' in (added[0].get('data') or {}).get('action_required', '')


def test_handle_tool_request_records_tool_routing(monkeypatch):
    monkeypatch.setattr('agent.cli_backends.workspace_mutation.tool_routing.prefer_read_tool',
                        lambda *, session, tool_name, arguments: ('codecompass.plan_context', {'query': 'q'}))
    turn = SimpleNamespace(row={}, message={'tool_name': 'repo.read_file_range', 'arguments': {}}, err='')
    session = SimpleNamespace(mode='strict_patch_request', task_prompt='x')
    chosen, arguments = tool_actions._route_read_tool(session, turn, 'repo.read_file_range', {'path': 'a.py'})
    assert chosen == 'codecompass.plan_context'
    assert turn.row['tool_routing'] == {'from': 'repo.read_file_range', 'to': 'codecompass.plan_context'}
