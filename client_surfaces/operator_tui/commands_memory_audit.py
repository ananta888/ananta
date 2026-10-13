"""Read-only memory audit command backed by the shared authenticated Hub API."""

import json
import os
from urllib.parse import quote, urlencode

from ananta_contracts.memory_access import require_memory_identifier
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.ops_api_client import OpsApiClient

_OPTIONS = {"run-id": "run_id", "task-id": "task_id", "trace-id": "trace_id",
            "channel-id": "channel_id", "scope": "scope", "page-size": "page_size",
            "after-sequence": "after_sequence", "view": "view"}
_USAGE = (':memory-audit <project-id> [--run-id ID] [--task-id ID] [--trace-id ID] '
          '[--channel-id ID] [--scope SCOPE] [--page-size 1..200] [--after-sequence N] '
          '[--view events|diffs]')


def handle_memory_audit_command(args: list[str], state: OperatorState) -> CommandResult:
    if not args or args[0] == 'help':
        return CommandResult(state, _USAGE, handled=True)
    try:
        project = require_memory_identifier(args[0], 'project_id')
        values = {}
        if len(args[1:]) % 2:
            raise ValueError('memory_audit_options_invalid')
        for index in range(1, len(args), 2):
            key = args[index].removeprefix('--')
            if not args[index].startswith('--') or key not in _OPTIONS or _OPTIONS[key] in values:
                raise ValueError('memory_audit_options_invalid')
            value = args[index + 1]
            if key in {'page-size', 'after-sequence'}:
                if not value.isascii() or not value.isdigit() or len(value) > 19:
                    raise ValueError('memory_audit_options_invalid')
                number = int(value)
                if not (1 <= number <= 200 if key == 'page-size' else 0 <= number < 2**63):
                    raise ValueError('memory_audit_options_invalid')
            else:
                require_memory_identifier(value, key)
                if key == 'scope' and value not in {'run', 'task', 'goal', 'conversation', 'workspace', 'global'}:
                    raise ValueError('memory_audit_options_invalid')
            values[_OPTIONS[key]] = value
        view = values.pop('view', 'events')
        if view not in {'events', 'diffs'} or view == 'diffs' and (
                not values.get('run_id') or int(values.get('after_sequence', '0'))):
            raise ValueError('memory_audit_options_invalid')
        audit = state.audit_context or {}
        header = state.header_logo_game or {}
        token = audit.get('token') or audit.get('auth_token') or header.get('token') or header.get('auth_token')
        client = OpsApiClient(state.endpoint, token=str(token or os.environ.get('ANANTA_AUTH_TOKEN') or ''))
        result = client.request_json('GET', '/api/projects/' + quote(project, safe='')
                                    + '/memory-audit/' + view + '?' + urlencode(values))
        payload = result.get('data', result)
        expected_schema = 'ananta.memory-diff-page.v1' if view == 'diffs' else 'ananta.memory-audit-page.v1'
        expected_integrity = 'snapshot_and_audit_links_verified' if view == 'diffs' else 'event_digests_verified'
        if (not isinstance(payload, dict) or payload.get('project_id') != project
                or payload.get('schema') != expected_schema or payload.get('integrity') != expected_integrity
                or view == 'diffs' and payload.get('run_id') != values['run_id']):
            raise ValueError('memory_audit_response_invalid')
    except ValueError:
        return CommandResult(state, 'Memory-Audit: ungültige Auswahl oder Antwort. ' + _USAGE, handled=False)
    except Exception:
        return CommandResult(state, 'Memory-Audit nicht verfügbar; Hub und Projekt-Verwaltungsrechte prüfen.',
                             handled=False)
    game = dict(state.header_logo_game or {})
    game['audit_viewer'] = {'active': True, 'mode': 'memory_audit',
        'title': 'Memory-Diffs' if view == 'diffs' else 'Memory-Zugriffe',
        'group': 'Audit Logs', 'text': json.dumps(payload, ensure_ascii=True, indent=2),
        'view_line_offset': 0, 'view_col_offset': 0}
    panels = dict(state.panel_states or {})
    panels['audit'] = PanelState.HEALTHY
    status = 'Memory-Diffs geladen.' if view == 'diffs' else 'Memory-Zugriffe geladen.'
    updated = state.with_updates(mode=OperatorMode.NORMAL, command_line='',
                                 section_id='audit', header_logo_game=game, focus=FocusPane.CONTENT,
                                 panel_states=panels,
                                 status_message=status)
    return CommandResult(updated, updated.status_message, handled=True)
