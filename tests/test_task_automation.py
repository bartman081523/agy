"""Tests for Task Automation and Toolchain Parity in AGY Mod."""
import os
import shutil
import tempfile
import unittest

from agy_modules.epistemic import (
    TASK_AUTOMATION_MANDATES,
    get_task_automation_mandate_text,
    install_epistemic_rule,
    install_task_automation_rule,
)


class TestTaskAutomation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_mandate_text_contains_core_directives(self):
        text = get_task_automation_mandate_text()
        self.assertIn("update_steps", text)
        self.assertIn("task.md", text)
        self.assertIn("Planning Mode", text)
        self.assertIn("manage_task", text)
        self.assertIn("Background Tasks", text)
        self.assertIn("in_progress", text)
        self.assertIn("done", text)

    def test_install_task_automation_rule(self):
        rule_path = install_task_automation_rule(self.test_dir)
        self.assertTrue(os.path.isfile(rule_path))
        with open(rule_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("name: task-automation-mandates", content)
        self.assertIn("trigger: always_on", content)
        self.assertIn("update_steps", content)
        self.assertIn("task.md", content)

    def test_install_epistemic_rule_also_deploys_task_automation(self):
        epistemic_path = install_epistemic_rule(self.test_dir)
        self.assertTrue(os.path.isfile(epistemic_path))
        task_rule_path = os.path.join(self.test_dir, "task_automation.md")
        self.assertTrue(os.path.isfile(task_rule_path))

    def test_global_and_workspace_rules_exist(self):
        repo_rule = os.path.join(os.path.dirname(__file__), "..", ".agents", "rules", "task_automation.md")
        self.assertTrue(os.path.exists(repo_rule), f"Expected {repo_rule} to exist")


if __name__ == "__main__":
    unittest.main()
