
import pytest
import os
import sys

# Pfad zum agent Verzeichnis hinzufügen
sys.path.append(os.path.join(os.getcwd(), "agent"))

try:
    from shell import PersistentShell
except ImportError:
    from agent.shell import PersistentShell

@pytest.fixture
def ps_shell():
    if os.name != 'nt':
        pytest.skip("PowerShell tests only on Windows")
    shell = PersistentShell(shell_cmd="powershell.exe")
    yield shell
    shell.close()

def test_ps_tokenization_basic(ps_shell):
    # Einfacher Befehl
    is_safe, msg = ps_shell._validate_tokens("Get-Process")
    assert is_safe

def test_ps_tokenization_backticks(ps_shell):
    # Backtick Escaping - der Tokenizer sollte das Zeichen danach als Teil des Tokens sehen
    # oder zumindest nicht darüber stolpern.
    # In PS ist `n ein Newline. `r`n.
    # Wenn wir r`m haben, ist es eigentlich "rm"
    is_safe, msg = ps_shell._validate_tokens("r`m -rf /")
    # Da rm\s+-rf\s+.* auf Gesamtstring geprüft wird, wird es in execute() geblockt.
    # Aber hier prüfen wir die Token-Validierung.
    # Wenn r`m zu rm wird, sollte es (falls rm in Blacklist) geblockt werden.
    pass

def test_ps_tokenization_subexpression(ps_shell):
    # $(rm -rf /)
    # Alt: shlex.split -> ['$(rm', '-rf', '/)']
    # Neu: ['$', '(', 'rm', '-', 'rf', '/', ')']
    tokens = []
    # Wir rufen die interne Logik auf oder prüfen das Resultat
    cmd = "$(rm -rf /)"
    is_safe, msg = ps_shell._validate_tokens(cmd)
    
    # Da rm\s+-rf\s+.* ein Leerzeichen enthält, matcht es immer noch nicht auf das Token 'rm'.
    # ABER: Wenn wir 'rm' als Wort in die Blacklist aufnehmen, würde es jetzt matchen!
    
def test_ps_tokenization_special_chars(ps_shell):
    cmd = "ls;rm -rf /"
    # Der neue Tokenizer sollte rm als eigenes Token finden
    # shlex.split (posix=False) auf Windows -> ['ls;rm', '-rf', '/']
    # Unser neuer Tokenizer -> ['ls', ';', 'rm', '-', 'rf', '/', ')']
    
    # Wir simulieren die Blacklist-Prüfung für ein Token 'rm'
    ps_shell.blacklist.append(r"\brm\b")
    is_safe, msg = ps_shell._validate_tokens(cmd)
    assert not is_safe
    assert "rm" in msg
