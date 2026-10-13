"""Hub facts for bounded BPMN edge decisions and terminal target observations."""


def emit_bpmn_edge_events(*, plan, request, state, emitter, evaluate):
    if not plan.metadata.get('bpmn_definition_hash'):
        return
    terminal = state.completed | state.skipped | set(state.failed)
    for edge in plan.edges:
        if edge.source not in terminal:
            continue
        if edge.source in state.skipped:
            decision = 'skipped'
        elif edge.source in state.failed:
            decision = 'blocked'
        else:
            result = evaluate(edge, state)
            if result.value is None:
                continue  # Routing owns invalid-condition failure; never guess a choice.
            decision = 'selected' if result.matches else 'skipped'
        epoch = (state.plan_revision, state.attempts.get(edge.source, 0), state.attempts.get(edge.target, 0))
        epoch_key = (epoch, state.bpmn_edge_epoch) if state.bpmn_edge_epoch else epoch
        statuses = [decision]
        if decision == 'selected':
            if edge.target in state.completed:
                statuses.append('completed')
            elif edge.target in state.failed:
                statuses.append('failed')
            elif edge.target in state.skipped:
                statuses.append('blocked')
        for status in statuses:
            emitter.emit(state, plan=plan, request=request, step_id=edge.target,
                event_type='workflow.bpmn.edge.' + status,
                dedupe_key=f'native:{request.run_id}:edge:{edge.edge_id}:{epoch_key}:{status}',
                payload={'edge_id': edge.edge_id, 'source_step_id': edge.source, 'target_step_id': edge.target,
                    'edge_status': status, 'status': 'active' if status == 'selected' else status,
                    'source_attempt': epoch[1], 'target_attempt': epoch[2],
                    'plan_hash': plan.plan_hash, 'definition_hash': plan.metadata['bpmn_definition_hash']})


def emit_bpmn_edge_restart(*, plan, request, state, command_id, emitter):
    """Reopen only unresolved transitions; the scheduler still owns selection."""
    if not plan.metadata.get('bpmn_definition_hash'):
        return
    state.bpmn_edge_epoch += 1
    for edge in plan.edges:
        if edge.target in state.completed or edge.target in state.skipped or edge.source in state.skipped:
            continue
        if any(node in state.failed for node in (edge.source, edge.target)):
            continue
        emitter.emit(state, plan=plan, request=request, step_id=edge.target,
            event_type='workflow.bpmn.edge.pending',
            dedupe_key=f'native:{request.run_id}:edge:{edge.edge_id}:retry:{command_id}',
            payload={'edge_id': edge.edge_id, 'source_step_id': edge.source, 'target_step_id': edge.target,
                'edge_status': 'pending', 'status': 'pending', 'command_id': command_id,
                'source_attempt': state.attempts.get(edge.source, 0),
                'target_attempt': state.attempts.get(edge.target, 0),
                'plan_hash': plan.plan_hash, 'definition_hash': plan.metadata['bpmn_definition_hash']})


def emit_bpmn_edge_cancellation(*, plan, request, state, command_id, emitter):
    """A signed Hub cancellation closes unresolved transitions, never known results."""
    if not plan.metadata.get('bpmn_definition_hash') or state.status != 'cancelled':
        return
    for edge in plan.edges:
        if edge.target in state.completed or edge.target in state.skipped:
            continue
        if edge.source in state.skipped or any(node in state.failed for node in (edge.source, edge.target)):
            continue
        emitter.emit(state, plan=plan, request=request, step_id=edge.target,
            event_type='workflow.bpmn.edge.cancelled',
            dedupe_key=f'native:{request.run_id}:edge:{edge.edge_id}:cancel:{command_id}',
            payload={'edge_id': edge.edge_id, 'source_step_id': edge.source, 'target_step_id': edge.target,
                'edge_status': 'cancelled', 'status': 'cancelled', 'command_id': command_id,
                'source_attempt': state.attempts.get(edge.source, 0),
                'target_attempt': state.attempts.get(edge.target, 0),
                'plan_hash': plan.plan_hash, 'definition_hash': plan.metadata['bpmn_definition_hash']})
