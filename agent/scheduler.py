import threading
import time
import json
import os
import logging
from typing import List, Set
from concurrent.futures import ThreadPoolExecutor
from agent.models import ScheduledTask
from agent.shell import get_shell, PersistentShell, get_shell_pool
from agent.config import settings
from agent.repository import scheduled_task_repo
from agent.db_models import ScheduledTaskDB

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
        # Bei DB-Nutzung speichern wir einzelne Tasks im add/execute, 
        # aber wir halten die Liste aktuell.
        pass

    def add_task(self, command: str, interval_seconds: int) -> ScheduledTaskDB:
        task = ScheduledTaskDB(
            command=command,
            interval_seconds=interval_seconds,
            next_run=time.time() + interval_seconds
        )
        task = scheduled_task_repo.save(task)
        with self.lock:
            self.tasks.append(task)
        return task

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
                    t for t in self.tasks 
                    if t.enabled and now >= t.next_run and t.id not in self.running_task_ids
                ]
            
            for task in tasks_to_run:
                self.running_task_ids.add(task.id)
                self.executor.submit(self._execute_task, task)
            
            time.sleep(1)

    def _execute_task(self, task: ScheduledTaskDB):
        try:
            logging.info(f"Executing scheduled task {task.id}: {task.command}")
            # Nutzen des Shell-Pools für effiziente Ressourcennutzung
            pool = get_shell_pool()
            shell = pool.acquire()
            try:
                output, code = shell.execute(task.command)
                logging.info(f"Scheduled task {task.id} finished with code {code}")
                
                task.last_run = time.time()
                task.next_run = task.last_run + task.interval_seconds
                scheduled_task_repo.save(task)
            finally:
                pool.release(shell)
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
