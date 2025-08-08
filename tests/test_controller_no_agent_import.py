import importlib
import sys
import unittest


class ControllerImportTests(unittest.TestCase):
    def test_controller_does_not_import_agent(self):
        sys.modules.pop("agent.ai_agent", None)
        cc = importlib.import_module("controller.controller")
        self.assertNotIn("agent.ai_agent", sys.modules)
        with cc.app.test_client() as client:
            resp = client.get("/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["status"], "ok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
