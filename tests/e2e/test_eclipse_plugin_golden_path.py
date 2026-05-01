from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.eclipse_ui_golden_path_runner import _jar_declares_golden_path_surfaces


def test_plugin_golden_path_surface_detection(tmp_path: Path) -> None:
    plugin_jar = tmp_path / "io.ananta.eclipse.runtime.jar"
    plugin_xml = """
    <plugin>
      <extension point="org.eclipse.ui.commands">
        <command id="io.ananta.eclipse.command.chat"/>
        <command id="io.ananta.eclipse.command.patch"/>
      </extension>
      <extension point="org.eclipse.ui.views">
        <view id="io.ananta.eclipse.view.chat"/>
        <view id="io.ananta.eclipse.view.task_list"/>
        <view id="io.ananta.eclipse.view.artifact"/>
        <view id="io.ananta.eclipse.view.approval_queue"/>
      </extension>
    </plugin>
    """
    with zipfile.ZipFile(plugin_jar, "w") as jar:
        jar.writestr("plugin.xml", plugin_xml)
        jar.writestr("META-INF/MANIFEST.MF", "Bundle-SymbolicName: io.ananta.eclipse.runtime\n")

    ok, missing = _jar_declares_golden_path_surfaces(plugin_jar)

    assert ok is True
    assert missing == []
