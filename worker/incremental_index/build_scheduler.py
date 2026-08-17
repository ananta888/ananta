"""
CodeCompass Incremental Index - Build Scheduler

Orchestrates incremental builds in the worker with priority scheduling.
"""
import json
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime
from enum import Enum


class BuildPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class BuildJob:
    """Represents a scheduled build job."""
    job_id: str
    profile_name: str
    change_set_id: str
    priority: BuildPriority
    status: str  # pending, running, completed, failed
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            'job_id': self.job_id,
            'profile_name': self.profile_name,
            'change_set_id': self.change_set_id,
            'priority': self.priority.name,
            'status': self.status,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'completed_at': self.completed_at
        }


class IncrementalBuildScheduler:
    """Schedules and manages incremental build jobs."""
    
    def __init__(self):
        self.pending_queue: List[BuildJob] = []
       .running_jobs: Dict[str, BuildJob] = {}
        self.completed_jobs: List[BuildJob] = []
        
    def schedule_build(
        self,
        profile_name: str,
        change_set_id: str,
        priority: BuildPriority = BuildPriority.NORMAL
    ) -> BuildJob:
        """Schedule a new incremental build job."""
        job_id = self._generate_job_id(profile_name, change_set_id)
        
        job = BuildJob(
            job_id=job_id,
            profile_name=profile_name,
            change_set_id=change_set_id,
            priority=priority,
            status='pending',
            created_at=datetime.utcnow().isoformat()
        )
        
        self.pending_queue.append(job)
        self.pending_queue.sort(key=lambda j: j.priority.value, reverse=True)
        
        return job
    
    def start_next_job(self) -> Optional[BuildJob]:
        """Start the highest priority pending job."""
        if not self.pending_queue:
            return None
        
        job = self.pending_queue.pop(0)
        job.status = 'running'
        job.started_at = datetime.utcnow().isoformat()
        
        self.running_jobs[job.job_id] = job
        return job
    
    def complete_job(self, job_id: str, success: bool = True) -> bool:
        """Mark a running job as completed."""
        if job_id not in self.running_jobs:
            return False
        
        job = self.running_jobs.pop(job_id)
        job.status = 'completed' if success else 'failed'
        job.completed_at = datetime.utcnow().isoformat()
        
        self.completed_jobs.append(job)
        
        # Keep only last 100 completed jobs
        if len(self.completed_jobs) > 100:
            self.completed_jobs = self.completed_jobs[-100:]
        
        return True
    
    def get_queue_status(self) -> Dict:
        """Get current queue status."""
        return {
            'pending_count': len(self.pending_queue),
            'running_count': len(self.running_jobs),
            'completed_count': len(self.completed_jobs),
            'pending_jobs': [j.to_dict() for j in self.pending_queue],
            'running_jobs': [j.to_dict() for j in self.running_jobs.values()]
        }
    
    def _generate_job_id(self, profile_name: str, change_set_id: str) -> str:
        """Generate unique job ID."""
        import hashlib
        content = f"{profile_name}:{change_set_id}:{datetime.utcnow().isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


if __name__ == '__main__':
    scheduler = IncrementalBuildScheduler()
    
    # Schedule some jobs
    job1 = scheduler.schedule_build('default', 'changeset_001', BuildPriority.HIGH)
    job2 = scheduler.schedule_build('default', 'changeset_002', BuildPriority.NORMAL)
    
    print("Queue Status:")
    print(json.dumps(scheduler.get_queue_status(), indent=2))
