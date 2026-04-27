from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.ai_agent import create_app
from agent.database import init_db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5861)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(args.data_dir).resolve() / 'evidence.db'}")
    os.environ.setdefault("CONTROLLER_URL", "http://mock-controller")
    os.environ.setdefault("AGENT_NAME", "evidence-hub")
    os.environ.setdefault("INITIAL_ADMIN_USER", "admin")
    os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin")

    init_db()
    app = create_app(agent="evidence-hub")
    app.config["AGENT_TOKEN"] = args.token
    app.config["TESTING"] = False
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG") or {}),
        "worker_runtime": {"workspace_root": str(Path(args.data_dir).resolve() / "worker-runtime")},
    }
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
