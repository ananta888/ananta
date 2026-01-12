import threading
import time
import json
import os
import logging
from typing import List
from agent.models import ScheduledTask
from agent.shell import get_shell
from agent.config import settings

class TaskScheduler:
    def __init__(self, persistence_file="data/scheduled_tasks.json"):
        self.persistence_file = persistence_file
        self.tasks: List[ScheduledTask] = []
        self.running = False
        self.thread = None
        self._load_tasks()

    def _load_tasks(self):
        if os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, "r") as f:
                    data = json.load(f)
                    self.tasks = [ScheduledTask(**t) for t in data]
                logging.info(f"Loaded {len(self.tasks)} scheduled tasks.")
            except Exception as e:
                logging.error(f"Error loading scheduled tasks: {e}")

    def _save_tasks(self):
        os.makedirs(os.path.dirname(self.persistence_file), exist_ok=True)
        try:
            with open(self.persistence_file, "w") as f:
                json.dump([t.model_dump() for t in self.tasks], f, indent=2)
        except Exception as e:
            logging.error(f"Error saving scheduled tasks: {e}")

    def add_task(self, command: str, interval_seconds: int) -> ScheduledTask:
        task = ScheduledTask(
            command=command,
            interval_seconds=interval_seconds,
            next_run=time.time() + interval_seconds
        )
        self.tasks.append(task)
        self._save_tasks()
        return task

    def remove_task(self, task_id: str):
        self.tasks = [t for t in self.tasks if t.id != task_id]
        self._save_tasks()

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
        logging.info("Scheduler stopped.")

    def _run_loop(self):
        while self.running:
            now = time.time()
            for task in self.tasks:
                if task.enabled and now >= task.next_run:
                    self._execute_task(task)
            time.sleep(1)

    def _execute_task(self, task: ScheduledTask):
        logging.info(f"Executing scheduled task {task.id}: {task.command}")
        shell = get_shell()
        output, code = shell.execute(task.command)
        logging.info(f"Scheduled task {task.id} finished with code {code}")
        
        task.last_run = time.time()
        task.next_run = task.last_run + task.interval_seconds
        self._save_tasks()

_scheduler_instance = None
def get_scheduler() -> TaskScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance
