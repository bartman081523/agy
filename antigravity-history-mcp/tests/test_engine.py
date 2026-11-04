"""
Tests for AntigravityHistoryEngine.
"""
import os
import shutil
import tempfile
import unittest

from engine import AntigravityHistoryEngine, _extract_task_items, _clean_workspace_path, _extract_project_name


class TestAntigravityHistoryEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_history.db")
        self.engine = AntigravityHistoryEngine(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_schema_initialization(self):
        rows = self.engine.query("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {r["name"] for r in rows}
        expected = {"sessions", "events", "snapshots", "tasks", "subagent_runs", "plans", "artifacts", "skills", "rules", "file_states"}
        self.assertTrue(expected.issubset(table_names))

    def test_workspace_normalization(self):
        self.assertEqual(_clean_workspace_path("file:///run/media/julian/ML6/agy"), "/run/media/julian/ML6/agy")
        self.assertEqual(_clean_workspace_path("/run/media/julian/ML6/agy"), "/run/media/julian/ML6/agy")
        self.assertEqual(_clean_workspace_path(""), "")

    def test_project_name_extraction(self):
        self.assertEqual(_extract_project_name("/run/media/julian/ML6/agy"), "agy")
        self.assertEqual(_extract_project_name("file:///run/media/julian/ML4/open-notebook"), "open-notebook")
        self.assertEqual(_extract_project_name("outside-of-project"), "outside-of-project")

    def test_task_parsing(self):
        sample = """
# Tasks
- [/] Phase 1: Setup <!-- id: 0 -->
  - [x] Step A <!-- id: 1 -->
  - [ ] Step B <!-- id: 2 -->
"""
        items = _extract_task_items(sample)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["status"], "in_progress")
        self.assertEqual(items[1]["status"], "completed")
        self.assertEqual(items[2]["status"], "pending")
        self.assertEqual(items[0]["task_id"], "0")
        self.assertEqual(items[1]["task_id"], "1")
        self.assertEqual(items[2]["task_id"], "2")

    def test_full_refresh_on_real_data(self):
        # Test real refresh
        real_engine = AntigravityHistoryEngine()
        res = real_engine.refresh()
        self.assertIn("Refreshed", res)
        # Verify sessions table is populated
        sessions = real_engine.query("SELECT count(*) as c FROM sessions")
        self.assertGreater(sessions[0]["c"], 0)


if __name__ == "__main__":
    unittest.main()
