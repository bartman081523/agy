"""Unit tests for AGY SymComp Module"""
import os
import unittest
import subprocess
from agy_modules.symcomp.compaction_engine import SymCompEngine, SYMCOMP_MARKER
from agy_modules.symcomp.rule_generator import install_symcomp_rule

class TestSymCompModule(unittest.TestCase):

    def test_compact_multiple_constructs(self):
        sample = (
            "<construct id=\"℧-a\">\n"
            "  <claim>Cache hit latency < 5ms</claim>\n"
            "</construct>\n"
            "<construct id=\"℧-b\">\n"
            "  <claim>Throughput > 1000 rps</claim>\n"
            "</construct>"
        )
        engine = SymCompEngine()
        compacted = engine.compact_constructs(sample, next_action="run performance benchmark")
        self.assertIn(SYMCOMP_MARKER, compacted)
        self.assertIn("Cache hit latency < 5ms", compacted)
        self.assertIn("Throughput > 1000 rps", compacted)
        self.assertIn("Next: run performance benchmark", compacted)

    def test_install_symcomp_rule(self):
        test_dir = "/tmp/agy_test_symcomp"
        rule_path = install_symcomp_rule(test_dir)
        self.assertTrue(os.path.exists(rule_path))
        with open(rule_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("symcomp-construct-compaction", content)

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-symcomp")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-SYMCOMP: active=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
