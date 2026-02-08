import os
import pytest
from unittest.mock import patch, MagicMock
from agent.shell import PersistentShell

def test_shell_cmd_linux_no_interactive():
    # Wir simulieren eine Linux-Umgebung
    with patch('os.name', 'posix'):
        with patch('shutil.which', return_value="/bin/bash"):
            with patch('subprocess.Popen') as mock_popen:
                # Mock Popen Instanz
                mock_proc = MagicMock()
                mock_popen.return_value = mock_proc
                
                shell = PersistentShell(shell_cmd="bash")
                
                # Prüfen welches Kommando an Popen ging
                args, kwargs = mock_popen.call_args
                cmd = args[0]
                
                assert cmd == ["bash"]
                # Sicherstellen dass -i NICHT drin ist
                assert "-i" not in cmd

def test_shell_execute_linux_status_code():
    with patch('os.name', 'posix'):
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None # Prozess läuft
            mock_proc.stdout.readline.side_effect = [
                "output line 1\n",
                "---CMD_FINISHED_marker--- 0\n",
                ""
            ]
            mock_popen.return_value = mock_proc
            
            with patch('uuid.uuid4', return_value="marker"):
                shell = PersistentShell(shell_cmd="bash")
                output, code = shell.execute("ls")
                
                assert output == "output line 1"
                assert code == 0
                
                # Prüfen was an stdin gesendet wurde
                mock_proc.stdin.write.assert_any_call("ls\necho ---CMD_FINISHED_marker--- $?\n")
