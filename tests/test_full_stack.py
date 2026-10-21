"""Integration tests for AGY Full-Stack and Modular Controllers"""
import os
import unittest
import subprocess

class TestFullStackAndModular(unittest.TestCase):

    def test_full_stack_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-full")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-FULL: active=1", proc.stdout)
        self.assertIn("infra=1", proc.stdout)
        self.assertIn("epistemic=1", proc.stdout)
        self.assertIn("symbolic=1", proc.stdout)
        self.assertIn("socratic=1", proc.stdout)
        self.assertIn("symcomp=1", proc.stdout)

    def test_full_stack_partial_disable(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-full")
        env = os.environ.copy()
        env["AGY_SYMCOMP"] = "0"
        env["AGY_SOCRATIC"] = "0"
        
        proc = subprocess.run([launcher, "--probe"], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("symcomp=0", proc.stdout)
        self.assertIn("socratic=0", proc.stdout)
        self.assertIn("epistemic=1", proc.stdout)

    def test_modular_list_variants(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-modular")
        
        proc = subprocess.run([launcher, "--list-variants"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("agy-infrastruktur", proc.stdout)
        self.assertIn("agy-epistemic", proc.stdout)
        self.assertIn("agy-symbolic", proc.stdout)
        self.assertIn("agy-socratic", proc.stdout)
        self.assertIn("agy-symcomp", proc.stdout)

if __name__ == "__main__":
    unittest.main()
