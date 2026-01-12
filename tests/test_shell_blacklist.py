import os
import sys

# Pfad zum agent Verzeichnis hinzufügen
sys.path.append(os.path.join(os.getcwd(), "agent"))

try:
    from shell import get_shell
except ImportError:
    # Falls wir im agent-Ordner sind
    from agent.shell import get_shell

def test_blacklist():
    shell = get_shell()
    
    # Teste erlaubten Befehl
    output, code = shell.execute("echo Hello World")
    print(f"Befehl: echo Hello World -> Code: {code}, Output: {output}")
    assert code == 0
    assert "Hello World" in output

    # Teste verbotenen Befehl (rm -rf / aus blacklist.txt)
    output, code = shell.execute("rm -rf /")
    print(f"Befehl: rm -rf / -> Code: {code}, Output: {output}")
    assert code == -1
    assert "Error: Command contains blacklisted pattern" in output

    # Teste Teilübereinstimmung
    output, code = shell.execute("ls && reboot")
    print(f"Befehl: ls && reboot -> Code: {code}, Output: {output}")
    assert code == -1
    assert "reboot" in output

    print("\nBlacklist-Test erfolgreich!")

if __name__ == "__main__":
    test_blacklist()
