import os
from agent.shell import PersistentShell

def test_powershell():
    if os.name != "nt":
        print("PowerShell test only running on Windows")
        return

    # Versuche powershell.exe zu finden
    shell = PersistentShell(shell_cmd="powershell.exe")
    
    # Teste einfachen Befehl
    output, code = shell.execute("Get-Host")
    print(f"Output: {output[:100]}...")
    print(f"Exit Code: {code}")
    assert code == 0
    assert "Microsoft.PowerShell" in output

    # Teste Error Handling
    output, code = shell.execute("Get-InvalidCommand")
    print(f"Output (Error Case): {output[:100]}...")
    print(f"Exit Code (Error Case): {code}")
    assert code != 0

    shell.close()
    print("PowerShell test successful!")

if __name__ == "__main__":
    test_powershell()
