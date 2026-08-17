"""
CodeCompass Worker Architecture Rollout

Worker-Integration für verteilte Architektur-Analyse und -Generierung.
Unterstützt parallele Verarbeitung von Architektur-Tasks über mehrere Worker-Nodes.

Author: CodeCompass Team
License: MIT
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
import hashlib
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class WorkerTaskType(str, Enum):
    """Typen von Architektur-Worker-Tasks"""
    GENERATE_SUMMARY = "generate_summary"
    EXTRACT_EVIDENCE = "extract_evidence"
    BUILD_SLICE = "build_slice"
    VALIDATE_HIERARCHY = "validate_hierarchy"
    COMPUTE_METRICS = "compute_metrics"
    UPDATE_INDEX = "update_index"
    SYNC_LAYER = "sync_layer"


class WorkerStatus(str, Enum):
    """Worker-Status"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass
class WorkerTask:
    """Ein Architektur-Worker-Task"""
    task_id: str
    task_type: WorkerTaskType
    node_ids: List[str]
    priority: int = 5
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "node_ids": self.node_ids,
            "priority": self.priority,
            "parameters": self.parameters,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "worker_id": self.worker_id,
            "duration": (self.completed_at or time.time()) - (self.started_at or self.created_at)
            if self.started_at else None
        }


@dataclass
class WorkerNode:
    """Ein Worker-Node im Architektur-Rollout"""
    worker_id: str
    capabilities: List[WorkerTaskType]
    status: WorkerStatus = WorkerStatus.IDLE
    current_task: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    last_heartbeat: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "capabilities": [c.value for c in self.capabilities],
            "status": self.status.value,
            "current_task": self.current_task,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "last_heartbeat": self.last_heartbeat,
            "uptime": time.time() - self.last_heartbeat if self.last_heartbeat else 0,
            "metadata": self.metadata
        }


class ArchitectureWorkerCoordinator:
    """
    Koordinator für verteilte Architektur-Worker
    
    Verwaltet Task-Queue, Worker-Zuweisung und Ergebnis-Aggregation
    für parallele Architektur-Verarbeitung.
    """
    
    def __init__(
        self,
        architecture_slice_service: Any,
        architecture_summary_service: Any,
        max_workers: int = 10,
        task_timeout: int = 300
    ):
        """
        Initialisiere Worker-Koordinator
        
        Args:
            architecture_slice_service: Service für Architektur-Slices
            architecture_summary_service: Service für Summaries
            max_workers: Maximale Anzahl aktiver Worker
            task_timeout: Timeout für Tasks in Sekunden
        """
        self.slice_service = architecture_slice_service
        self.summary_service = architecture_summary_service
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        
        self.workers: Dict[str, WorkerNode] = {}
        self.task_queue: List[WorkerTask] = []
        self.completed_tasks: Dict[str, WorkerTask] = {}
        self.failed_tasks: Dict[str, WorkerTask] = {}
        
        logger.info(
            f"ArchitectureWorkerCoordinator initialized with "
            f"max_workers={max_workers}, task_timeout={task_timeout}s"
        )
    
    def _generate_task_id(self, task_type: WorkerTaskType, node_ids: List[str]) -> str:
        """Generiere deterministische Task-ID"""
        content = f"{task_type.value}:{','.join(sorted(node_ids))}:{time.time()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def register_worker(
        self,
        worker_id: str,
        capabilities: List[WorkerTaskType],
        metadata: Optional[Dict[str, Any]] = None
    ) -> WorkerNode:
        """
        Registriere einen Worker-Node
        
        Args:
            worker_id: Eindeutige Worker-ID
            capabilities: Unterstützte Task-Typen
            metadata: Optionale Metadaten
            
        Returns:
            Registrierter WorkerNode
        """
        worker = WorkerNode(
            worker_id=worker_id,
            capabilities=capabilities,
            metadata=metadata or {}
        )
        
        self.workers[worker_id] = worker
        logger.info(f"Registered worker {worker_id} with {len(capabilities)} capabilities")
        
        return worker
    
    def unregister_worker(self, worker_id: str) -> bool:
        """Deregistriere einen Worker-Node"""
        if worker_id not in self.workers:
            return False
        
        worker = self.workers[worker_id]
        if worker.status == WorkerStatus.BUSY:
            logger.warning(f"Unregistering busy worker {worker_id}")
        
        del self.workers[worker_id]
        logger.info(f"Unregistered worker {worker_id}")
        
        return True
    
    def submit_task(
        self,
        task_type: WorkerTaskType,
        node_ids: List[str],
        priority: int = 5,
        parameters: Optional[Dict[str, Any]] = None
    ) -> WorkerTask:
        """
        Submitte einen neuen Task
        
        Args:
            task_type: Typ des Tasks
            node_ids: Betroffene Node-IDs
            priority: Priorität (1=hoch, 10=niedrig)
            parameters: Task-spezifische Parameter
            
        Returns:
            Erstellter WorkerTask
        """
        task = WorkerTask(
            task_id=self._generate_task_id(task_type, node_ids),
            task_type=task_type,
            node_ids=node_ids,
            priority=priority,
            parameters=parameters or {}
        )
        
        # Füge in priorisierte Queue ein
        self.task_queue.append(task)
        self.task_queue.sort(key=lambda t: (t.priority, t.created_at))
        
        logger.debug(f"Submitted task {task.task_id} ({task_type.value})")
        
        return task
    
    def _find_available_worker(self, task: WorkerTask) -> Optional[WorkerNode]:
        """Finde verfügbaren Worker für Task"""
        available = [
            w for w in self.workers.values()
            if w.status == WorkerStatus.IDLE and
            task.task_type in w.capabilities
        ]
        
        if not available:
            return None
        
        # Wähle Worker mit wenigsten completed tasks (Load Balancing)
        return min(available, key=lambda w: w.tasks_completed)
    
    def _execute_task(self, task: WorkerTask, worker: WorkerNode) -> Dict[str, Any]:
        """
        Führe Task auf Worker aus
        
        Args:
            task: Auszuführender Task
            worker: Ausführender Worker
            
        Returns:
            Task-Ergebnis
        """
        task.status = "running"
        task.worker_id = worker.worker_id
        task.started_at = time.time()
        worker.status = WorkerStatus.BUSY
        worker.current_task = task.task_id
        
        try:
            result = {}
            
            if task.task_type == WorkerTaskType.GENERATE_SUMMARY:
                result = self._execute_generate_summary(task)
            elif task.task_type == WorkerTaskType.EXTRACT_EVIDENCE:
                result = self._execute_extract_evidence(task)
            elif task.task_type == WorkerTaskType.BUILD_SLICE:
                result = self._execute_build_slice(task)
            elif task.task_type == WorkerTaskType.VALIDATE_HIERARCHY:
                result = self._execute_validate_hierarchy(task)
            elif task.task_type == WorkerTaskType.COMPUTE_METRICS:
                result = self._execute_compute_metrics(task)
            elif task.task_type == WorkerTaskType.UPDATE_INDEX:
                result = self._execute_update_index(task)
            elif task.task_type == WorkerTaskType.SYNC_LAYER:
                result = self._execute_sync_layer(task)
            else:
                raise ValueError(f"Unknown task type: {task.task_type}")
            
            task.status = "completed"
            task.result = result
            task.completed_at = time.time()
            
            worker.tasks_completed += 1
            worker.status = WorkerStatus.IDLE
            worker.current_task = None
            
            logger.info(f"Task {task.task_id} completed successfully")
            
            return result
            
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.completed_at = time.time()
            
            worker.tasks_failed += 1
            worker.status = WorkerStatus.ERROR
            worker.current_task = None
            
            logger.error(f"Task {task.task_id} failed: {e}")
            
            raise
    
    def _execute_generate_summary(self, task: WorkerTask) -> Dict[str, Any]:
        """Führe Summary-Generierung aus"""
        results = []
        
        for node_id in task.node_ids:
            summary_result = self.summary_service.get_or_create_summary(
                node_id=node_id,
                max_tokens=task.parameters.get("max_tokens", 200)
            )
            results.append({
                "node_id": node_id,
                "summary": summary_result.get("summary"),
                "cached": summary_result.get("cached", False)
            })
        
        return {"summaries": results}
    
    def _execute_extract_evidence(self, task: WorkerTask) -> Dict[str, Any]:
        """Führe Evidence-Extraktion aus"""
        results = []
        
        for node_id in task.node_ids:
            slice_result = self.slice_service.select_slice(
                seed_node_ids=[node_id],
                max_nodes=task.parameters.get("max_nodes", 5),
                strategy=task.parameters.get("strategy", "GRAPH_NEIGHBORHOOD")
            )
            
            evidence = []
            for node in slice_result.get("nodes", []):
                if node.get("evidence_snippets"):
                    evidence.extend(node["evidence_snippets"])
            
            results.append({
                "node_id": node_id,
                "evidence_count": len(evidence),
                "evidence": evidence[:10]  # Max 10 snippets
            })
        
        return {"evidence_results": results}
    
    def _execute_build_slice(self, task: WorkerTask) -> Dict[str, Any]:
        """Führe Slice-Building aus"""
        slice_result = self.slice_service.select_slice(
            seed_node_ids=task.node_ids,
            max_nodes=task.parameters.get("max_nodes", 50),
            strategy=task.parameters.get("strategy", "HYBRID")
        )
        
        return {
            "slice_id": hashlib.sha256(
                ','.join(task.node_ids).encode()
            ).hexdigest()[:16],
            "node_count": len(slice_result.get("nodes", [])),
            "edge_count": len(slice_result.get("edges", []))
        }
    
    def _execute_validate_hierarchy(self, task: WorkerTask) -> Dict[str, Any]:
        """Validiere Architektur-Hierarchie"""
        # Placeholder für Hierarchie-Validierung
        return {
            "valid": True,
            "node_count": len(task.node_ids),
            "issues": []
        }
    
    def _execute_compute_metrics(self, task: WorkerTask) -> Dict[str, Any]:
        """Berechne Architektur-Metriken"""
        # Placeholder für Metrik-Berechnung
        return {
            "metrics": {
                "avg_depth": 3.5,
                "branching_factor": 4.2,
                "coupling_score": 0.75
            }
        }
    
    def _execute_update_index(self, task: WorkerTask) -> Dict[str, Any]:
        """Aktualisiere Architektur-Index"""
        # Placeholder für Index-Update
        return {
            "updated_nodes": len(task.node_ids),
            "index_version": "1.0.0"
        }
    
    def _execute_sync_layer(self, task: WorkerTask) -> Dict[str, Any]:
        """Synchronisiere Architektur-Layer"""
        # Placeholder für Layer-Sync
        return {
            "synced_layers": 1,
            "layer_id": task.parameters.get("layer_id", "default")
        }
    
    def process_queue(self, max_tasks: int = 10) -> int:
        """
        Verarbeite Task-Queue
        
        Args:
            max_tasks: Maximale Anzahl zu verarbeitender Tasks
            
        Returns:
            Anzahl verarbeiteter Tasks
        """
        processed = 0
        active_workers = sum(
            1 for w in self.workers.values()
            if w.status == WorkerStatus.BUSY
        )
        
        while processed < max_tasks and self.task_queue:
            if active_workers >= self.max_workers:
                break
            
            task = self.task_queue.pop(0)
            worker = self._find_available_worker(task)
            
            if not worker:
                # Re-queue task
                self.task_queue.append(task)
                continue
            
            try:
                self._execute_task(task, worker)
                self.completed_tasks[task.task_id] = task
                processed += 1
                active_workers -= 1
            except Exception as e:
                self.failed_tasks[task.task_id] = task
                logger.error(f"Task {task.task_id} failed during execution: {e}")
        
        logger.info(f"Processed {processed} tasks")
        
        return processed
    
    def get_worker_stats(self) -> Dict[str, Any]:
        """Hole Worker-Statistiken"""
        total_workers = len(self.workers)
        idle_workers = sum(1 for w in self.workers.values() if w.status == WorkerStatus.IDLE)
        busy_workers = sum(1 for w in self.workers.values() if w.status == WorkerStatus.BUSY)
        error_workers = sum(1 for w in self.workers.values() if w.status == WorkerStatus.ERROR)
        
        return {
            "total_workers": total_workers,
            "idle_workers": idle_workers,
            "busy_workers": busy_workers,
            "error_workers": error_workers,
            "queued_tasks": len(self.task_queue),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "workers": [w.to_dict() for w in self.workers.values()]
        }
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Hole Status eines spezifischen Tasks"""
        if task_id in self.completed_tasks:
            return self.completed_tasks[task_id].to_dict()
        elif task_id in self.failed_tasks:
            return self.failed_tasks[task_id].to_dict()
        else:
            pending = [t for t in self.task_queue if t.task_id == task_id]
            if pending:
                return pending[0].to_dict()
        
        return None


def create_worker_coordinator(
    architecture_slice_service: Any,
    architecture_summary_service: Any,
    config: Optional[Dict[str, Any]] = None
) -> ArchitectureWorkerCoordinator:
    """
    Factory-Funktion zum Erstellen eines Worker-Koordinators
    
    Args:
        architecture_slice_service: Instanz des ArchitectureSliceService
        architecture_summary_service: Instanz des ArchitectureSummaryService
        config: Optionale Konfiguration
        
    Returns:
        Konfigurierter ArchitectureWorkerCoordinator
    """
    config = config or {}
    
    coordinator = ArchitectureWorkerCoordinator(
        architecture_slice_service=architecture_slice_service,
        architecture_summary_service=architecture_summary_service,
        max_workers=config.get("max_workers", 10),
        task_timeout=config.get("task_timeout", 300)
    )
    
    # Registriere Default-Worker
    default_capabilities = list(WorkerTaskType)
    coordinator.register_worker(
        worker_id="default-worker-1",
        capabilities=default_capabilities,
        metadata={"type": "local", "version": "1.0.0"}
    )
    
    return coordinator
