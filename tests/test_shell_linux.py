import sys
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agent.shell import PersistentShell


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-spezifische Shell-Tests werden auf Windows übersprungen")
def test_shell_cmd_linux_no_interactive():
    # Wir simulieren eine Linux-Umgebung
    with patch("os.name", "posix"):
        with patch("shutil.which", return_value="/bin/bash"):
            with patch("subprocess.Popen") as mock_popen:
                # Mock Popen Instanz
                mock_proc = MagicMock()
                mock_proc.poll.return_value = 0
                mock_proc.stdout.readline.side_effect = [""]
                mock_popen.return_value = mock_proc

                shell = PersistentShell(shell_cmd="bash")

                # Prüfen welches Kommando an Popen ging
                args, kwargs = mock_popen.call_args
                cmd = args[0]

                assert cmd == ["bash", "--noprofile", "--norc"]
                # Sicherstellen dass -i NICHT drin ist
                assert "-i" not in cmd
                shell.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-spezifische Shell-Tests werden auf Windows übersprungen")
def test_shell_execute_linux_status_code():
    with patch("os.name", "posix"):
        with patch.object(PersistentShell, "_load_blacklist"), patch.object(PersistentShell, "_start_process"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None  # Prozess läuft

            with patch("uuid.uuid4", return_value="marker"):
                shell = PersistentShell(shell_cmd="bash")
                shell.process = mock_proc
                shell.output_queue = Queue()

                def queue_command_output(command: str):
                    if command == "ls\necho ---CMD_FINISHED_marker--- $?\n":
                        shell.output_queue.put("output line 1\n")
                        shell.output_queue.put("---CMD_FINISHED_marker--- 0\n")

                mock_proc.stdin.write.side_effect = queue_command_output
                output, code = shell.execute("ls")

                assert output == "output line 1"
                assert code == 0

                # Prüfen was an stdin gesendet wurde
                mock_proc.stdin.write.assert_any_call("ls\necho ---CMD_FINISHED_marker--- $?\n")
                shell.close()
