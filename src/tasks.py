import logging
from typing import Dict, Any, List, Optional

from src.db import get_conn
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

class TaskStore:
    """Verwaltet die Aufgaben im Controller."""

    def add_task(self, task: str, agent: Optional[str] = None, 
                 template: Optional[str] = None) -> Dict[str, Any]:
        """Fügt eine neue Aufgabe hinzu.

        Args:
            task: Die Aufgabenbeschreibung
            agent: Optional, der zuständige Agent
            template: Optional, das zu verwendende Template

        Returns:
            Die erstellte Aufgabe als Dictionary
        """
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO controller.tasks (task, agent, template) VALUES (%s, %s, %s)",
                (task, agent, template)
            )
            conn.commit()
            entry = {"task": task}
            if agent:
                entry["agent"] = agent
            if template:
                entry["template"] = template
            return entry
        except Exception as e:
            conn.rollback()
            logger.error("Fehler beim Hinzufügen der Aufgabe: %s", e)
            raise
        finally:
            cur.close()
            conn.close()

    def next_task(self, agent: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Ruft die nächste Aufgabe für einen Agenten ab.

        Args:
            agent: Optional, der anfragende Agent

        Returns:
            Die nächste Aufgabe oder None, wenn keine verfügbar ist
        """
        conn = get_conn()
        cur = conn.cursor()
        try:
            # Suche nach spezifischen Aufgaben für diesen Agenten
            if agent:
                cur.execute(
                    "SELECT id, task, agent, template FROM controller.tasks "
                    "WHERE agent = %s ORDER BY id LIMIT 1",
                    (agent,)
                )
                row = cur.fetchone()
                if row:
                    task_id, task, agent_name, template = row
                    cur.execute("DELETE FROM controller.tasks WHERE id = %s", (task_id,))
                    conn.commit()
                    result = {"task": task}
                    if agent_name:
                        result["agent"] = agent_name
                    if template:
                        result["template"] = template
                    return result

            # Suche nach allgemeinen Aufgaben ohne spezifischen Agenten
            cur.execute(
                "SELECT id, task, agent, template FROM controller.tasks "
                "WHERE agent IS NULL ORDER BY id LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                task_id, task, agent_name, template = row
                cur.execute("DELETE FROM controller.tasks WHERE id = %s", (task_id,))
                conn.commit()
                result = {"task": task}
                if agent_name:
                    result["agent"] = agent_name
                if template:
                    result["template"] = template
                return result

            return None  # Keine passende Aufgabe gefunden
        except Exception as e:
            conn.rollback()
            logger.error("Fehler beim Abrufen der nächsten Aufgabe: %s", e)
            return None
        finally:
            cur.close()
            conn.close()

    def list_tasks(self, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        """Listet alle Aufgaben auf.

        Args:
            agent: Optional, Filter für einen bestimmten Agenten

        Returns:
            Liste von Aufgaben-Dictionaries
        """
        conn = get_conn()
        cur = conn.cursor()
        try:
            if agent:
                cur.execute(
                    "SELECT task, agent, template FROM controller.tasks "
                    "WHERE agent = %s OR agent IS NULL ORDER BY id",
                    (agent,)
                )
            else:
                cur.execute(
                    "SELECT task, agent, template FROM controller.tasks ORDER BY id"
                )

            tasks = []
            for row in cur.fetchall():
                task, agent_name, template = row
                entry = {"task": task}
                if agent_name:
                    entry["agent"] = agent_name
                if template:
                    entry["template"] = template
                tasks.append(entry)
            return tasks
        except Exception as e:
            logger.error("Fehler beim Auflisten der Aufgaben: %s", e)
            return []
        finally:
            cur.close()
            conn.close()
