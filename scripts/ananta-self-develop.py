#!/usr/bin/env python3
"""Small operator client; all admission, scheduling and publication stay in Hub."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['form', 'import', 'submit', 'start', 'stop', 'status'])
    parser.add_argument('file', nargs='?', type=Path)
    parser.add_argument('--url', default=os.environ.get('ANANTA_BASE_URL', 'http://127.0.0.1:5000'))
    local_token = Path.home() / 'ananta-data/workflow-runtime-dev/hub/hub-service-token'
    parser.add_argument('--token-file', type=Path, default=os.environ.get('ANANTA_HUB_TOKEN_FILE')
                        or (local_token if local_token.is_file() else None))
    args = parser.parse_args()
    if not args.token_file:
        parser.error('--token-file or ANANTA_HUB_TOKEN_FILE is required')
    if args.action == 'submit' and not args.file:
        parser.error('submit requires a JSON file')
    action = 'import' if args.action == 'submit' else args.action
    payload = {'document': json.loads(args.file.read_text())} if args.action == 'submit' else {}
    request = Request(args.url.rstrip('/') + '/api/self-development/' + action,
        data=None if action in {'form', 'status'} else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + args.token_file.read_text().strip(),
                 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=120) as response:
            print(json.dumps(json.load(response)['data'], indent=2, ensure_ascii=False))
    except HTTPError as exc:
        print('Hub request failed: HTTP ' + str(exc.code), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
