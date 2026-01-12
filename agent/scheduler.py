import threading
import time
import json
import os
import logging
from typing import List, Set
from concurrent.futures import ThreadPoolExecutor
from agent.models import ScheduledTask
from agent.shell import get_shell, PersistentShell
from agent.config import settings

class TaskScheduler:
    def __init__(self, persistence_file="data/scheduled_tasks.json"):
        self.persistence_file = persistence_file
        self.tasks: List[ScheduledTask] = []
        self.running = False
        self.thread = None
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.running_task_ids: Set[str] = set()
        self.lock = threading.Lock()
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
        with self.lock:
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
        with self.lock:
            self.tasks.append(task)
        self._save_tasks()
        return task

    def remove_task(self, task_id: str):
        with self.lock:
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
        self.executor.shutdown(wait=False)
        logging.info("Scheduler stopped.")

    def _run_loop(self):
        while self.running:
            now = time.time()
            with self.lock:
                tasks_to_run = [
                    t for t in self.tasks 
                    if t.enabled and now >= t.next_run and t.id not in self.running_task_ids
                ]
            
            for task in tasks_to_run:
                self.running_task_ids.add(task.id)
                self.executor.submit(self._execute_task, task)
            
            time.sleep(1)

    def _execute_task(self, task: ScheduledTask):
        try:
            logging.info(f"Executing scheduled task {task.id}: {task.command}")
            # Nutzen einer eigenen Shell-Instanz für parallele Ausführung
            shell = PersistentShell()
            try:
                output, code = shell.execute(task.command)
                logging.info(f"Scheduled task {task.id} finished with code {code}")
                
                task.last_run = time.time()
                task.next_run = task.last_run + task.interval_seconds
                self._save_tasks()
            finally:
                shell.close()
        except Exception as e:
            logging.error(f"Error executing scheduled task {task.id}: {e}")
        finally:
            self.running_task_ids.remove(task.id)

_scheduler_instance = None
def get_scheduler() -> TaskScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance
