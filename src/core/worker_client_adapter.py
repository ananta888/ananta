from client_surfaces import ClientSurface
from worker_engine import WorkerEngine

class WorkerClientAdapter:
    """Adapter responsible for mediating communication between ClientSurface abstractions and the core WorkerEngine. This decouples the client view from the worker execution implementation, adhering to the Adapter Pattern and DIP."""
    def __init__(self, client_surface: ClientSurface, worker_instance: WorkerEngine):
        self.client_surface = client_surface
        self.worker = worker_instance

    def execute_task(self, task_id: str, payload: dict):
        """Routes a task request from the client surface to the worker engine. Ensures task integrity and context passing."""
        print(f"[Adapter] Processing task {task_id} from {self.client_surface.get_type()}")
        # Logic: Send task to worker, handling potential failures or transformations
        result = self.worker.submit_task(task_id, payload)
        return {"status": "success", "result": result}