"""
Tests for Antigravity MCP Server tools.
"""
import json
import unittest

import server


def parse_mcp_output(raw_str: str) -> dict:
    """Strip ```json fencing and load json."""
    lines = raw_str.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return json.loads("\n".join(lines))


class TestAntigravityMCPTools(unittest.TestCase):
    def test_get_context(self):
        res = server.get_context(limit=3)
        data = parse_mcp_output(res)
        self.assertIn("sessions", data)
        self.assertLessEqual(len(data["sessions"]), 3)
        if data["sessions"]:
            s = data["sessions"][0]
            self.assertIn("session_id", s)
            self.assertIn("workspace", s)
            self.assertIn("title", s)

    def test_search_history(self):
        # Search for a ubiquitous term like "git" or "test"
        res = server.search_history(query="git", limit=5)
        data = parse_mcp_output(res)
        self.assertIn("hits", data)
        self.assertLessEqual(len(data["hits"]), 5)
        if data["hits"]:
            h = data["hits"][0]
            self.assertIn("session_id", h)
            self.assertIn("step_index", h)
            self.assertIn("preview", h)

    def test_get_session_meta(self):
        ctx_data = parse_mcp_output(server.get_context(limit=1))
        if ctx_data["sessions"]:
            sid = ctx_data["sessions"][0]["session_id"]
            meta_res = server.get_session_meta(session_id=sid)
            meta_data = parse_mcp_output(meta_res)
            self.assertIn("session", meta_data)
            self.assertEqual(meta_data["session"]["session_id"], sid)

    def test_get_turn_and_turns(self):
        ctx_data = parse_mcp_output(server.get_context(limit=1))
        if ctx_data["sessions"]:
            sid = ctx_data["sessions"][0]["session_id"]
            turns_res = server.get_turns(session_id=sid, start_index=-3)
            turns_data = parse_mcp_output(turns_res)
            if "turns" in turns_data and turns_data["turns"]:
                first_turn = turns_data["turns"][0]
                turn_idx = first_turn["step_index"]
                turn_res = server.get_turn(session_id=sid, step_index=turn_idx, include_thinking=True)
                turn_data = parse_mcp_output(turn_res)
                self.assertEqual(turn_data["session_id"], sid)
                self.assertEqual(turn_data["step_index"], turn_idx)

    def test_search_patches_and_get_patches(self):
        patches_res = server.get_patches(limit=5)
        patches_data = parse_mcp_output(patches_res)
        self.assertIn("files", patches_data)
        if patches_data["files"]:
            p = patches_data["files"][0]
            self.assertIn("session_id", p)
            self.assertIn("file_path", p)

    def test_list_tasks(self):
        res = server.list_tasks(limit=10)
        data = parse_mcp_output(res)
        self.assertIn("tasks", data)
        if data["tasks"]:
            t = data["tasks"][0]
            self.assertIn("subject", t)
            self.assertIn("status", t)

    def test_skills_tools(self):
        list_res = server.list_skills()
        list_data = parse_mcp_output(list_res)
        self.assertIn("skills", list_data)
        self.assertGreater(list_data["count"], 0)

        # Search skills
        search_res = server.search_skills(query="antigravity", limit=3)
        search_data = parse_mcp_output(search_res)
        self.assertIn("skills", search_data)

    def test_rules_tools(self):
        list_res = server.list_rules()
        list_data = parse_mcp_output(list_res)
        self.assertIn("rules", list_data)
        self.assertGreater(list_data["count"], 0)


if __name__ == "__main__":
    unittest.main()
