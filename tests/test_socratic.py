"""Unit tests for AGY Socratic Dialog Module"""
import os
import unittest
import subprocess
from agy_modules.socratic.dialog_moves import SOCRATIC_DIALOG_PREAMBLE, get_socratic_dialog_text
from agy_modules.socratic.rule_generator import install_socratic_rule

class TestSocraticModule(unittest.TestCase):

    def test_preamble_contains_three_moves(self):
        text = get_socratic_dialog_text()
        self.assertIn("Sokratische Aporie", text)
        self.assertIn("Platonischer Ideal-Check", text)
        self.assertIn("Aristotelische Self-Critique", text)
        self.assertIn("Channel Guard", text)

    def test_install_socratic_rule(self):
        test_dir = "/tmp/agy_test_socratic"
        rule_path = install_socratic_rule(test_dir)
        self.assertTrue(os.path.exists(rule_path))
        with open(rule_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("sokratischer-dialog-moves", content)
        self.assertIn("Productive Impasse", content)

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-socratic")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-SOCRATIC: active=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
