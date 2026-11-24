"""Unit tests for Claude & Antigravity Skills Synchronizer (skills_sync.py)"""
import os
import tempfile
import unittest
try:
    import yaml
except ImportError:
    yaml = None
from agy_modules.infrastruktur.skills_sync import (
    normalize_skill_frontmatter,
    sync_claude_skills,
)


class TestSkillsSync(unittest.TestCase):

    def test_normalize_skill_without_frontmatter(self):
        content = """# devmind

## Zweck

Sub-Skill für den Prompt-Mind DevMind v2.0 Unified — sprachagnostische Engineering-Haltung.

## Instructions
Execute cleanly.
"""
        norm, modified = normalize_skill_frontmatter(content, "devmind")
        self.assertTrue(modified)
        self.assertTrue(norm.startswith("---\n"))
        parts = norm.split("---", 2)
        parsed = yaml.safe_load(parts[1])
        self.assertEqual(parsed["name"], "devmind")
        self.assertIn("DevMind", parsed["description"])
        self.assertIn("# devmind", parts[2])

    def test_normalize_skill_with_invalid_yaml(self):
        # YAML error due to unquoted colon in description
        invalid_content = """---
name: test-skill
description: Methodik: curl + JSON-Mining (NICHT WebFetch): Doppelpunkte ohne Quotes brechen YAML.
---
# Test Skill Body
"""
        norm, modified = normalize_skill_frontmatter(invalid_content, "test-skill")
        self.assertTrue(modified)
        parts = norm.split("---", 2)
        parsed = yaml.safe_load(parts[1])
        self.assertEqual(parsed["name"], "test-skill")
        self.assertIn("curl + JSON-Mining", parsed["description"])

    def test_normalize_already_valid_frontmatter(self):
        valid_content = """---
name: clean-skill
description: 'A completely clean description.'
---
# Clean Skill
"""
        norm, modified = normalize_skill_frontmatter(valid_content, "clean-skill")
        self.assertFalse(modified)
        self.assertEqual(norm, valid_content)

    def test_sync_claude_skills_temporary_directory(self):
        with tempfile.TemporaryDirectory() as tmp_src:
            with tempfile.TemporaryDirectory() as tmp_cfg:
                with tempfile.TemporaryDirectory() as tmp_ws:
                    os.makedirs(os.path.join(tmp_ws, ".git"))
                    # Create two mock skills: one valid, one missing frontmatter
                    s1 = os.path.join(tmp_src, "valid-skill")
                    os.makedirs(s1)
                    with open(os.path.join(s1, "SKILL.md"), "w") as f:
                        f.write("---\nname: valid-skill\ndescription: A valid skill\n---\nBody")

                    s2 = os.path.join(tmp_src, "missing-fm-skill")
                    os.makedirs(s2)
                    with open(os.path.join(s2, "SKILL.md"), "w") as f:
                        f.write("# missing-fm-skill\n\nThis is the description paragraph.\n")

                    res = sync_claude_skills(
                        claude_skills_dir=tmp_src,
                        gemini_config_dir=tmp_cfg,
                        workspace_dir=tmp_ws,
                    )
                    self.assertEqual(res["status"], "synced")
                    self.assertEqual(res["total_skills"], 2)
                    self.assertEqual(res["valid"], 2)
                    self.assertEqual(res["normalized"], 1)

                    # Verify workspace .agents/skills linkage was created
                    ws_skills_link = os.path.join(tmp_ws, ".agents", "skills")
                    self.assertTrue(os.path.exists(ws_skills_link))

    def test_live_claude_skills_all_valid(self):
        claude_dir = os.path.expanduser("~/.claude/skills")
        if not os.path.exists(claude_dir):
            self.skipTest("Live ~/.claude/skills not available")

        res = sync_claude_skills(claude_skills_dir=claude_dir, dry_run=True)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["valid"], res["total_skills"])
        self.assertGreater(res["total_skills"], 50)


if __name__ == "__main__":
    unittest.main()
