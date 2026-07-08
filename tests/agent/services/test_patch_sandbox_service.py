from pathlib import Path

from agent.services.patch_sandbox_service import PatchSandboxService


def test_patch_sandbox_applies_patch_without_mutating_original(tmp_path):
    original = tmp_path / "x.py"
    original.write_text("value = 1\n", encoding="utf-8")
    patch = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    result = PatchSandboxService().create_sandbox(workspace_dir=tmp_path, patch_text=patch)
    assert result["status"] == "completed"
    assert original.read_text(encoding="utf-8") == "value = 1\n"
    assert (Path(result["sandbox_dir"]) / "x.py").read_text(encoding="utf-8") == "value = 2\n"


def test_patch_sandbox_blocks_path_escape(tmp_path):
    patch = """diff --git a/../x b/../x
--- a/../x
+++ b/../x
@@ -1 +1 @@
-a
+b
"""
    result = PatchSandboxService().create_sandbox(workspace_dir=tmp_path, patch_text=patch)
    assert result["status"] == "failed"
    assert result["reason_code"] == "unsafe_patch_path"
