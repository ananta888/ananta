import json
import logging
import os
import time

import portalocker

from agent.common.utils.json_utils import update_json
from agent.config import settings


def archive_terminal_logs(data_dir: str) -> None:
    """Archiviert alte Einträge aus dem Terminal-Log."""
    log_file = os.path.join(data_dir, "terminal_log.jsonl")
    if not os.path.exists(log_file):
        return

    archive_file = log_file.replace(".jsonl", "_archive.jsonl")
    retention_days = settings.tasks_retention_days
    cutoff = time.time() - (retention_days * 86400)

    try:
        remaining_entries = []
        archived_entries = []

        with portalocker.Lock(
            log_file, mode="r+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
        ) as f:
            lines = f.readlines()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", time.time()) < cutoff:
                        archived_entries.append(line)
                    else:
                        remaining_entries.append(line)
                except Exception:
                    remaining_entries.append(line)

            if archived_entries:
                logging.info(f"Archiviere {len(archived_entries)} Terminal-Log Einträge.")
                with portalocker.Lock(
                    archive_file,
                    mode="a",
                    encoding="utf-8",
                    timeout=5,
                    flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
                ) as archive_locked:
                    for line in archived_entries:
                        archive_locked.write(line)

                f.seek(0)
                f.truncate()
                for line in remaining_entries:
                    f.write(line)
    except Exception as e:
        logging.error(f"Fehler bei der Archivierung des Terminal-Logs: {e}")


def cleanup_old_backups(data_dir: str):
    """Löscht alte Datenbank-Backups basierend auf backups_retention_days."""
    try:
        backup_dir = os.path.join(data_dir, "backups")
        if not os.path.exists(backup_dir):
            return

        retention_days = settings.backups_retention_days
        cutoff = time.time() - (retention_days * 86400)

        removed_count = 0
        for filename in os.listdir(backup_dir):
            file_path = os.path.join(backup_dir, filename)
            if os.path.isfile(file_path):
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff:
                    try:
                        os.remove(file_path)
                        removed_count += 1
                    except Exception as e:
                        logging.error(f"Fehler beim Löschen der Backup-Datei {file_path}: {e}")

        if removed_count > 0:
            logging.info(f"Cleanup: {removed_count} alte Backups aus {backup_dir} entfernt.")
    except Exception as e:
        logging.error(f"Fehler beim Cleanup der Backups: {e}")


def archive_old_tasks(tasks_path: str):
    """Archiviert alte Tasks basierend auf dem Alter."""
    from agent.db_models import ArchivedTaskDB
    from agent.repository import archived_task_repo, task_repo

    retention_days = settings.tasks_retention_days
    now = time.time()
    cutoff_active = now - (retention_days * 86400)
    cleanup_days = settings.archived_tasks_retention_days
    cutoff_archive = now - (cleanup_days * 86400)

    # 1. Datenbank-Archivierung bevorzugen
    try:
        archived_task_repo.delete_old(cutoff_archive)
        old_tasks = task_repo.get_old_tasks(cutoff_active)
        if old_tasks:
            logging.info(f"Archiviere {len(old_tasks)} Tasks aus der Datenbank.")
            for t in old_tasks:
                try:
                    archived = ArchivedTaskDB(**t.model_dump())
                    archived_task_repo.save(archived)
                    task_repo.delete(t.id)
                except Exception as e:
                    logging.error(f"Fehler beim Archivieren von Task {t.id}: {e}")
        return
    except Exception as e:
        logging.debug(f"DB-Archivierung nicht möglich/fehlgeschlagen: {e}")

    # 2. JSON-Fallback
    if not os.path.exists(tasks_path):
        return

    archive_path = tasks_path.replace(".json", "_archive.json")

    def cleanup_archive_func(archived_tasks):
        if not isinstance(archived_tasks, dict):
            return archived_tasks
        remaining = {
            tid: t
            for tid, t in archived_tasks.items()
            if t.get("archived_at", t.get("created_at", now)) >= cutoff_archive
        }
        removed = len(archived_tasks) - len(remaining)
        if removed > 0:
            logging.info(f"Cleanup: {removed} sehr alte archivierte Tasks aus JSON entfernt.")
        return remaining

    if os.path.exists(archive_path):
        update_json(archive_path, cleanup_archive_func, default={})

    def archive_func(tasks):
        if not isinstance(tasks, dict):
            return tasks
        to_archive = {tid: t for tid, t in tasks.items() if t.get("created_at", now) < cutoff_active}
        remaining = {tid: t for tid, t in tasks.items() if t.get("created_at", now) >= cutoff_active}

        if to_archive:
            logging.info(f"Archiviere {len(to_archive)} Tasks in {archive_path}")

            def update_archive(archive_data):
                if not isinstance(archive_data, dict):
                    archive_data = {}
                for tid, tdata in to_archive.items():
                    tdata.setdefault("archived_at", now)
                    archive_data[tid] = tdata
                return archive_data

            update_json(archive_path, update_archive, default={})
            return remaining
        return tasks

    update_json(tasks_path, archive_func, default={})
