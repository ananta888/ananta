import os
from client_surfaces.operator_tui.app import _splash_disabled, _play_splash_to_terminal
from client_surfaces.operator_tui.models import OperatorMode, FocusPane, OperatorState
import argparse

args = argparse.Namespace(skip_splash=False)
print('ANANTA_TUI_SPLASH env:', repr(os.environ.get('ANANTA_TUI_SPLASH', '(not set)')))
print('splash disabled:', _splash_disabled(args))
print('os.isatty(0):', os.isatty(0))

state = OperatorState(
    endpoint='http://localhost:5000', auth_state='unset',
    mode=OperatorMode.NORMAL, focus=FocusPane.NAVIGATION, section_id='dashboard'
)
print('-- playing animation now --')
_play_splash_to_terminal(state)
print('-- animation done --')
