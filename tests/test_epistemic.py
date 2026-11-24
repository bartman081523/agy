"""Unit tests for AGY Epistemic Module"""
import os
import unittest
import subprocess
from agy_modules.epistemic.scimind_mandates import SCIMIND_5_0_MANDATES, get_epistemic_mandate_text
from agy_modules.epistemic.rule_generator import install_epistemic_rule

class TestEpistemicModule(unittest.TestCase):

    def test_mandate_contains_core_epistemic_clauses(self):
        text = get_epistemic_mandate_text()
        self.assertIn("Incomplete Suggestion Protocol", text)
        self.assertIn("Empirical Verification", text)
        self.assertIn("Falsificationism", text)
        self.assertIn("Operational Robustness", text)
        self.assertIn("Zero-Trust Environment", text)
        self.assertIn("Anti-Embedding", text)

    def test_install_epistemic_rule(self):
        test_dir = "/tmp/agy_test_rules"
        rule_path = install_epistemic_rule(test_dir)
        self.assertTrue(os.path.exists(rule_path))
        with open(rule_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("epistemic-humility-mandates", content)
        self.assertIn("SciMind 5.0", content)

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-epistemic")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-EPISTEMIC: active=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
