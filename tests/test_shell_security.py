import unittest
from agent.shell import PersistentShell
import os

class TestShellSanitization(unittest.TestCase):
    def setUp(self):
        self.shell = PersistentShell()

    def test_blacklist_simple(self):
        # 'rm -rf' ist in blacklist.txt (als regex 'rm\s+-rf\s+.*')
        out, code = self.shell.execute("rm -rf /")
        self.assertIn("Error: Command matches blacklisted pattern", out)
        self.assertEqual(code, -1)

    def test_argument_injection(self):
        # Versuche eine Injektion, die nicht direkt in der Blacklist steht
        # Zum Beispiel: echo "hello" ; ls
        # Derzeit erlaubt die Shell das, loggt es aber nur.
        # FEAT-028 soll bessere Erkennung bieten.
        out, code = self.shell.execute("echo 'hello' ; ls")
        # Wenn wir es strenger machen wollen, sollte das vielleicht blockiert werden,
        # oder zumindest die Injektion erkannt werden.
        print(f"Output for injection: {out}, code: {code}")

    def test_complex_injection(self):
        # Beispiel für Argument-Injektion in einen Befehl
        # git log --author="`whoami`"
        out, code = self.shell.execute('git log --author="`whoami`"')
        print(f"Output for complex injection: {out}, code: {code}")

    def test_token_blacklist(self):
        # find . -exec rm -rf {} \;
        # Hier ist rm -rf ein Token (bzw. mehrere)
        out, code = self.shell.execute("find . -exec rm -rf {} \\;")
        self.assertIn("Error: Command matches blacklisted pattern", out)
        self.assertEqual(code, -1)

    def test_variable_concatenation(self):
        # Schutz gegen $a$b
        out, code = self.shell.execute("a=rm; b=-rf; $a$b /")
        self.assertIn("Error: Variablen-Verkettung ($a$b) ist aus Sicherheitsgründen deaktiviert.", out)
        self.assertEqual(code, -1)

if __name__ == "__main__":
    unittest.main()
