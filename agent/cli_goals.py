#!/usr/bin/env python3
"""
CLI Tool for submitting goals to the Ananta Auto-Planner.

Usage:
    python -m agent.cli_goals "Implement user authentication"
    python -m agent.cli_goals --goal "Add API endpoint" --context "Using Flask" --team dev
    python -m agent.cli_goals --list
    python -m agent.cli_goals --status
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests
from agent.config import settings


def get_base_url():
    return f"http://localhost:{settings.port}"


def get_auth_token(base_url: str) -> str:
    username = os.environ.get("ANANTA_USER", "admin")
    password = os.environ.get("ANANTA_PASSWORD", "admin")

    response = requests.post(f"{base_url}/login", json={"username": username, "password": password}, timeout=10)

    if response.status_code != 200:
        print(f"Error: Login failed - {response.status_code}")
        sys.exit(1)

    data = response.json().get("data", {})
    return data.get("access_token", "")


def submit_goal(goal: str, context: str = None, team_id: str = None, create_tasks: bool = True):
    base_url = get_base_url()
    token = get_auth_token(base_url)

    headers = {"Authorization": f"Bearer {token}"}

    payload = {"goal": goal, "create_tasks": create_tasks}
    if context:
        payload["context"] = context
    if team_id:
        payload["team_id"] = team_id

    response = requests.post(f"{base_url}/tasks/auto-planner/plan", json=payload, headers=headers, timeout=60)

    if response.status_code in (200, 201):
        data = response.json().get("data", {})
        print(f"Goal submitted: {goal}")
        print(f"Tasks created: {len(data.get('created_task_ids', []))}")
        for task_id in data.get("created_task_ids", []):
            print(f"  - {task_id}")
        return data.get("created_task_ids", [])
    else:
        print(f"Error: {response.status_code} - {response.text}")
        return []


def list_goals():
    base_url = get_base_url()
    token = get_auth_token(base_url)

    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(f"{base_url}/tasks/auto-planner/status", headers=headers, timeout=10)

    if response.status_code == 200:
        data = response.json().get("data", {})
        stats = data.get("stats", {})
        print("Auto-Planner Status:")
        print(f"  Enabled: {data.get('enabled', False)}")
        print(f"  Goals Processed: {stats.get('goals_processed', 0)}")
        print(f"  Tasks Created: {stats.get('tasks_created', 0)}")
        print(f"  Follow-ups Created: {stats.get('followups_created', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
    else:
        print(f"Error: {response.status_code}")


def list_tasks(status: str = None, limit: int = 20):
    base_url = get_base_url()
    token = get_auth_token(base_url)

    headers = {"Authorization": f"Bearer {token}"}

    params = {"limit": limit}
    if status:
        params["status"] = status

    response = requests.get(f"{base_url}/tasks", headers=headers, params=params, timeout=10)

    if response.status_code == 200:
        tasks = response.json()
        if isinstance(tasks, dict):
            tasks = tasks.get("data", [])

        print(f"Tasks ({len(tasks)}):")
        for task in tasks[:limit]:
            task_id = task.get("id", "N/A")
            title = task.get("title", "N/A")[:50]
            task_status = task.get("status", "N/A")
            print(f"  [{task_status:12}] {task_id}: {title}")
    else:
        print(f"Error: {response.status_code}")


def main():
    parser = argparse.ArgumentParser(
        description="CLI Tool for Ananta Auto-Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Submit a goal:
    python -m agent.cli_goals "Implement user login"

  With context and team:
    python -m agent.cli_goals --goal "Add API" --context "Flask" --team backend

  List tasks:
    python -m agent.cli_goals --tasks

  Check status:
    python -m agent.cli_goals --status
""",
    )

    parser.add_argument("goal", nargs="?", help="Goal description to submit")
    parser.add_argument("--goal", "-g", dest="goal_flag", help="Goal description (alternative)")
    parser.add_argument("--context", "-c", help="Additional context for the goal")
    parser.add_argument("--team", "-t", help="Team ID to assign tasks to")
    parser.add_argument("--no-create", action="store_true", help="Don't create tasks, just analyze")
    parser.add_argument("--status", "-s", action="store_true", help="Show Auto-Planner status")
    parser.add_argument("--tasks", action="store_true", help="List recent tasks")
    parser.add_argument("--task-status", help="Filter tasks by status")
    parser.add_argument("--limit", "-l", type=int, default=20, help="Limit number of results")

    args = parser.parse_args()

    if args.status:
        list_goals()
    elif args.tasks:
        list_tasks(status=args.task_status, limit=args.limit)
    elif args.goal or args.goal_flag:
        goal_text = args.goal or args.goal_flag
        create_tasks = not args.no_create
        submit_goal(goal=goal_text, context=args.context, team_id=args.team, create_tasks=create_tasks)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
