"""Integration tests for AGY Symbolic-Epistemic Suite"""
import os
import unittest
import subprocess

class TestSymbolicEpistemicSuite(unittest.TestCase):

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-symbolic-epistemic")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-SYMBOLIC-EPISTEMIC: active=1", proc.stdout)
        self.assertIn("epistemic=1", proc.stdout)
        self.assertIn("symbolic=1", proc.stdout)
        self.assertIn("socratic=1", proc.stdout)

    def test_env_var_overrides(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-symbolic-epistemic")
        env = os.environ.copy()
        env["AGY_SYMBOLIC"] = "0"
        
        proc = subprocess.run([launcher, "--probe"], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("symbolic=0", proc.stdout)
        self.assertIn("epistemic=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
