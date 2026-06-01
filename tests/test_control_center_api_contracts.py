from io import BytesIO
from agent.repository import artifact_repo


def test_control_center_projects_tasks_sessions_workers_contract(client, admin_auth_header):
    projects_res = client.get('/api/projects', headers=admin_auth_header)
    assert projects_res.status_code == 200
    payload = projects_res.get_json()
    assert payload['status'] == 'success'
    assert 'items' in payload['data']

    create_task_res = client.post(
        '/api/tasks',
        headers=admin_auth_header,
        json={'title': 'CC Contract Task', 'description': 'contract test', 'status': 'backlog', 'priority': 'High'},
    )
    assert create_task_res.status_code == 201
    task = create_task_res.get_json()['data']['task']
    assert task['id']

    task_detail_res = client.get(f"/api/tasks/{task['id']}", headers=admin_auth_header)
    assert task_detail_res.status_code == 200
    detail = task_detail_res.get_json()['data']
    assert 'task' in detail
    assert 'verification' in detail
    assert 'sessions' in detail

    create_session_res = client.post(
        f"/api/tasks/{task['id']}/sessions",
        headers=admin_auth_header,
        json={'title': 'CC Agent Session'},
    )
    assert create_session_res.status_code == 201
    created_session = create_session_res.get_json()['data']['session']
    assert created_session['task_id'] == task['id']
    assert created_session['session_kind'] == 'agent_execution'
    assert created_session.get('policy_snapshot')
    assert created_session['policy_snapshot']['runtime_boundary'] in {'local-only', 'cloud-allowed', 'remote', 'unknown'}

    sessions_res = client.get('/api/sessions', headers=admin_auth_header)
    assert sessions_res.status_code == 200
    sessions_payload = sessions_res.get_json()['data']
    assert 'items' in sessions_payload
    assert any(str(item.get('id') or '') == str(created_session['id']) for item in sessions_payload['items'])

    sessions_filtered_res = client.get(f"/api/sessions?task_id={task['id']}", headers=admin_auth_header)
    assert sessions_filtered_res.status_code == 200
    filtered_items = sessions_filtered_res.get_json()['data']['items']
    assert any(str(item.get('task_id') or '') == str(task['id']) for item in filtered_items)

    workers_res = client.get('/api/workers', headers=admin_auth_header)
    assert workers_res.status_code == 200
    workers_payload = workers_res.get_json()['data']
    assert 'items' in workers_payload


def test_control_center_policy_and_scope_contract(client, admin_auth_header):
    policies_res = client.get('/api/policies', headers=admin_auth_header)
    assert policies_res.status_code == 200
    policies = policies_res.get_json()['data']
    assert 'items' in policies

    scopes_res = client.get('/api/codecompass/context-scopes', headers=admin_auth_header)
    assert scopes_res.status_code == 200
    scopes = scopes_res.get_json()['data']
    assert scopes['count'] >= 1

    preview_res = client.post(
        '/api/codecompass/context-scopes/preview',
        headers=admin_auth_header,
        json={'include': ['/agent/**'], 'exclude': ['/.env', '/secrets/**']},
    )
    assert preview_res.status_code == 200
    preview = preview_res.get_json()['data']['scope_preview']
    assert 'excluded_sensitive_paths' in preview


def test_control_center_narrow_approval_contract(client, admin_auth_header):
    bad_res = client.post(
        '/api/policy/approve',
        headers=admin_auth_header,
        json={'action_id': 'a1', 'tool_call_id': 't1', 'scope': 'all_actions'},
    )
    assert bad_res.status_code == 403

    create_task_res = client.post(
        '/api/tasks',
        headers=admin_auth_header,
        json={'title': 'Approval task', 'description': 'contract test', 'status': 'backlog', 'priority': 'High'},
    )
    assert create_task_res.status_code == 201
    task_id = create_task_res.get_json()['data']['task']['id']

    create_session_res = client.post(
        f'/api/tasks/{task_id}/sessions',
        headers=admin_auth_header,
        json={'title': 'Approval session'},
    )
    assert create_session_res.status_code == 201
    session_id = create_session_res.get_json()['data']['session']['id']

    decisions_res = client.get(f'/api/sessions/{session_id}/policy-decisions', headers=admin_auth_header)
    assert decisions_res.status_code == 200
    pending = [
        item for item in decisions_res.get_json()['data']['items']
        if str(item.get('decision') or '') == 'require_approval' and item.get('tool_call_id') and item.get('action_id')
    ]
    assert pending
    chosen = pending[0]

    not_found_res = client.post(
        '/api/policy/approve',
        headers=admin_auth_header,
        json={'action_id': 'missing-action', 'tool_call_id': chosen['tool_call_id'], 'session_id': session_id, 'scope': 'single_action'},
    )
    assert not_found_res.status_code == 404

    ok_res = client.post(
        '/api/policy/approve',
        headers=admin_auth_header,
        json={'action_id': chosen['action_id'], 'tool_call_id': chosen['tool_call_id'], 'session_id': session_id, 'scope': 'single_action'},
    )
    assert ok_res.status_code == 200
    data = ok_res.get_json()['data']
    assert data['approved'] is True
    assert data['scope'] == 'single_action'

    conflict_res = client.post(
        '/api/policy/approve',
        headers=admin_auth_header,
        json={'action_id': chosen['action_id'], 'tool_call_id': chosen['tool_call_id'], 'session_id': session_id, 'scope': 'single_action'},
    )
    assert conflict_res.status_code == 409


def test_artifact_content_normalized_contract(client, admin_auth_header):
    upload_res = client.post(
        '/artifacts/upload',
        headers=admin_auth_header,
        data={'file': (BytesIO(b'hello artifact content'), 'hello.txt')},
        content_type='multipart/form-data',
    )
    assert upload_res.status_code == 201
    artifact_id = upload_res.get_json()['data']['artifact']['id']

    content_res = client.get(
        f'/artifacts/{artifact_id}/content?normalized=true&offset=0&limit=1024',
        headers=admin_auth_header,
    )
    assert content_res.status_code == 200
    data = content_res.get_json()['data']
    assert data['encoding'] == 'base64'
    assert data['type']
    assert 'payload' in data


def test_artifact_filters_contract(client, admin_auth_header):
    upload_a = client.post(
        '/artifacts/upload',
        headers=admin_auth_header,
        data={'file': (BytesIO(b'a1'), 'a1.txt')},
        content_type='multipart/form-data',
    )
    upload_b = client.post(
        '/artifacts/upload',
        headers=admin_auth_header,
        data={'file': (BytesIO(b'b1'), 'b1.txt')},
        content_type='multipart/form-data',
    )
    assert upload_a.status_code == 201
    assert upload_b.status_code == 201
    artifact_a = upload_a.get_json()['data']['artifact']
    artifact_b = upload_b.get_json()['data']['artifact']

    row_a = artifact_repo.get_by_id(str(artifact_a['id']))
    row_b = artifact_repo.get_by_id(str(artifact_b['id']))
    assert row_a is not None
    assert row_b is not None
    row_a.artifact_metadata = {'project_id': 'p1', 'task_id': 't1', 'session_id': 's1', 'type': 'log'}
    row_b.artifact_metadata = {'project_id': 'p2', 'task_id': 't2', 'session_id': 's2', 'type': 'text'}
    artifact_repo.save(row_a)
    artifact_repo.save(row_b)

    filtered = client.get('/artifacts?task_id=t1&session_id=s1&type=log', headers=admin_auth_header)
    assert filtered.status_code == 200
    filtered_rows = filtered.get_json()['data']
    ids = {row['id'] for row in filtered_rows}
    assert artifact_a['id'] in ids
    assert artifact_b['id'] not in ids


def test_control_center_event_stream_contract(client, admin_auth_header):
    response = client.get('/api/events/stream', headers=admin_auth_header)
    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'
