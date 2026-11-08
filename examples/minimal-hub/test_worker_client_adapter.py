import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# The example package directory carries a hyphen, so it cannot be imported by
# dotted path; load the adapter straight from its file location instead.
_spec = importlib.util.spec_from_file_location(
    "minimal_hub_worker_client_adapter",
    Path(__file__).resolve().parent / "core" / "worker_client_adapter.py",
)
if _spec is None or _spec.loader is None:  # pragma: no cover - file sits next to this test
    raise ImportError("minimal_hub_worker_client_adapter_unavailable")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
WorkerClientAdapter = _module.WorkerClientAdapter


class TestWorkerClientAdapter(unittest.TestCase):
    """Tests the WorkerClientAdapter ensuring correct delegation and handling of worker input."""

    def setUp(self):
        # Setup mocks for dependencies to isolate the adapter's unit responsibility
        self.mock_client = MagicMock()
        self.mock_worker = MagicMock()
        self.adapter = WorkerClientAdapter(self.mock_client, self.mock_worker)

    def test_execution_success_path(self):
        test_id = "test_task_123"
        payload = {"reason": "initial run", "data": "test"}
        expected_result = {"status": "success", "result": "Task completed successfully"}

        # Mock the worker's submission result
        self.mock_worker.submit_task.return_value = "Task completed successfully"

        # Execute the method under test
        result = self.adapter.execute_task(test_id, payload)

        # Assertions: Verify that the mock was called exactly once with the correct arguments
        self.mock_worker.submit_task.assert_called_once_with(test_id, payload)
        self.assertEqual(result, expected_result)

    def test_execution_failure_path(self):
        test_id = "fail_task"
        payload = {"data": "fail"}

        # Mock the worker failing to submit
        self.mock_worker.submit_task.side_effect = Exception("Worker service unavailable")

        # Note: For robustness, the adapter should probably wrap this in a try/except, but for this test, we check the exception propagation.
        with self.assertRaises(Exception) as e:
            self.adapter.execute_task(test_id, payload)

        self.assertIn("Worker service unavailable", str(e.exception))
