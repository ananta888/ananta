import os
import sys

# Pfad zum agent Verzeichnis hinzufügen
sys.path.append(os.path.join(os.getcwd(), "agent"))

try:
    from shell import PersistentShell
except ImportError:
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

    # Teste verschachtelten Befehl mit Fehler
    output, code = shell.execute("Get-Item -Path 'C:\\NichtExistierend'; echo 'Danach'")
    print(f"Output (Nested Error): {output}")
    print(f"Exit Code (Nested Error): {code}")
    # Aktuell wird das wahrscheinlich code 0 liefern, da echo 'Danach' erfolgreich ist
    # Wir wollen aber, dass code != 0 ist, wenn IRGENDWAS im Pfad fehlschlägt (je nach Einstellung)
    
    shell.close()
    print("PowerShell test successful!")

if __name__ == "__main__":
    test_powershell()
