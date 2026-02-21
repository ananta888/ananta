import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set

from agent.db_models import ScheduledTaskDB
from agent.repository import scheduled_task_repo
from agent.shell import get_shell_pool


class TaskScheduler:
    def __init__(self):
        self.tasks: List[ScheduledTaskDB] = []
        self.running = False
        self.thread = None
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.running_task_ids: Set[str] = set()
        self.lock = threading.Lock()
        self._load_tasks()

    def _load_tasks(self):
        try:
            self.tasks = scheduled_task_repo.get_all()
            logging.info(f"Loaded {len(self.tasks)} scheduled tasks from DB.")
        except Exception as e:
            logging.error(f"Error loading scheduled tasks: {e}")

    def _save_tasks(self):
        pass

    def add_task(
        self, command: str, interval_seconds: int, task_type: str = "shell", goal_context: Optional[str] = None
    ) -> ScheduledTaskDB:
        task = ScheduledTaskDB(
            command=command,
            interval_seconds=interval_seconds,
            next_run=time.time() + interval_seconds,
            enabled=True,
        )
        if goal_context:
            task.command = f"{task.command} | context: {goal_context}"
        task = scheduled_task_repo.save(task)
        with self.lock:
            self.tasks.append(task)
        return task

    def add_goal_task(
        self, goal: str, interval_seconds: int, context: Optional[str] = None, team_id: Optional[str] = None
    ) -> ScheduledTaskDB:
        payload = f"goal:{goal}"
        if context:
            payload += f"|context:{context}"
        if team_id:
            payload += f"|team:{team_id}"
        return self.add_task(payload, interval_seconds, task_type="goal")

    def remove_task(self, task_id: str):
        if scheduled_task_repo.delete(task_id):
            with self.lock:
                self.tasks = [t for t in self.tasks if t.id != task_id]

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            logging.info("Scheduler started.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        self.executor.shutdown(wait=False)
        logging.info("Scheduler stopped.")

    def _run_loop(self):
        while self.running:
            now = time.time()
            with self.lock:
                tasks_to_run = [
                    t for t in self.tasks if t.enabled and now >= t.next_run and t.id not in self.running_task_ids
                ]

            for task in tasks_to_run:
                self.running_task_ids.add(task.id)
                self.executor.submit(self._execute_task, task)

            time.sleep(1)

    def _parse_goal_command(self, command: str) -> dict:
        result = {"goal": "", "context": "", "team_id": ""}
        if not command.startswith("goal:"):
            return result

        parts = command[5:].split("|")
        result["goal"] = parts[0].strip() if parts else ""
        for part in parts[1:]:
            if part.startswith("context:"):
                result["context"] = part[8:].strip()
            elif part.startswith("team:"):
                result["team_id"] = part[5:].strip()
        return result

    def _execute_goal_task(self, task: ScheduledTaskDB) -> bool:
        try:
            from agent.routes.tasks.auto_planner import auto_planner

            parsed = self._parse_goal_command(task.command)
            if not parsed["goal"]:
                logging.warning(f"Invalid goal command in task {task.id}")
                return False

            result = auto_planner.plan_goal(
                goal=parsed["goal"],
                context=parsed["context"] or None,
                team_id=parsed["team_id"] or None,
                create_tasks=True,
            )
            created = len(result.get("created_task_ids", []))
            logging.info(f"Scheduled goal task {task.id} created {created} tasks")
            return True
        except Exception as e:
            logging.error(f"Error executing goal task {task.id}: {e}")
            return False

    def _execute_task(self, task: ScheduledTaskDB):
        try:
            command = task.command or ""

            if command.startswith("goal:"):
                self._execute_goal_task(task)
            else:
                logging.info(f"Executing scheduled task {task.id}: {command}")
                pool = get_shell_pool()
                shell = pool.acquire()
                try:
                    output, code = shell.execute(command)
                    logging.info(f"Scheduled task {task.id} finished with code {code}")
                finally:
                    pool.release(shell)

            task.last_run = time.time()
            task.next_run = task.last_run + task.interval_seconds
            scheduled_task_repo.save(task)
        except Exception as e:
            logging.error(f"Error executing scheduled task {task.id}: {e}")
        finally:
            self.running_task_ids.discard(task.id)


_scheduler_instance = None


def get_scheduler() -> TaskScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance
