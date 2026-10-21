"""Unit tests for AGY Infrastructure Module"""
import os
import unittest
import subprocess
from agy_modules.infrastruktur.context_manager import ContextManager
from agy_modules.infrastruktur.audio_token_guard import AudioTokenGuard
from agy_modules.infrastruktur.mcp_sync import sync_mcp_servers

class TestInfrastrukturModule(unittest.TestCase):

    def test_audio_token_guard_token_estimation(self):
        text = "Hello world! This is a test."
        tokens = AudioTokenGuard.estimate_text_tokens(text)
        self.assertGreaterEqual(tokens, 1)

    def test_audio_token_guard_payload_bound(self):
        small_data = b"RIFF" + b"\x00" * 100
        res = AudioTokenGuard.sanitize_audio_payload(small_data)
        self.assertTrue(res["valid"])

        huge_data = b"\x00" * (11 * 1024 * 1024)
        res_huge = AudioTokenGuard.sanitize_audio_payload(huge_data)
        self.assertFalse(res_huge["valid"])
        self.assertIn("exceeds", res_huge["error"])

    def test_context_manager_instance(self):
        cm = ContextManager("/non/existent/history.db")
        sessions = cm.list_recent_sessions(limit=5)
        self.assertEqual(sessions, [])

    def test_launcher_probe(self):
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_dir, "bin", "agy-infrastruktur")
        self.assertTrue(os.path.exists(launcher))
        
        proc = subprocess.run([launcher, "--probe"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AGY-INFRASTRUCTURE: active=1", proc.stdout)

if __name__ == "__main__":
    unittest.main()
