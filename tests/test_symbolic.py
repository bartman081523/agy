"""Unit tests for AGY Symbolic Module"""
import os
import unittest
import subprocess
from agy_modules.symbolic.cognito_constructs import SYMBOLIC_NOTATION, extract_constructs
from agy_modules.symbolic.rule_generator import install_symbolic_rule

class TestSymbolicModule(unittest.TestCase):

    def test_notation_grammar(self):
        self.assertIn("<construct id=\"℧-<label>\"", SYMBOLIC_NOTATION)
        self.assertIn("ESCALATE", SYMBOLIC_NOTATION)
        self.assertIn("Do Not Surface", SYMBOLIC_NOTATION)

    def test_extract_constructs(self):
        sample = (
            "Thinking...\n"
            "<construct id=\"℧-prime-lemma\" status=\"verified\">\n"
            "  <claim>Every even number > 2 is a sum of two primes.</claim>\n"
            "  <evidence source=\"file\">Goldbach conjecture verification file.</evidence>\n"
            "</construct>\n"
            "Continuing..."
        )
        parsed = extract_constructs(sample)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], "℧-prime-lemma")
        self.assertEqual(parsed[0]["status"], "verified")
        self.assertIn("Goldbach", parsed[0]["evidence"])

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-symbolic")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-SYMBOLIC: active=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
