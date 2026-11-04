"""
Integration test for stdio MCP protocol against run_server.sh.
"""
import json
import subprocess
import unittest


class TestStdioMCPProtocol(unittest.TestCase):
    def test_initialize_and_list_tools(self):
        proc = subprocess.Popen(
            ["/run/media/julian/ML6/agy/antigravity-history-mcp/run_server.sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        }

        try:
            # Send initialize
            proc.stdin.write(json.dumps(init_request) + "\n")
            proc.stdin.flush()

            # Read response
            line = proc.stdout.readline()
            resp = json.loads(line)
            self.assertEqual(resp.get("id"), 1)
            self.assertIn("result", resp)
            self.assertEqual(resp["result"]["serverInfo"]["name"], "AntigravityHistory")

            # Send initialized notification
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()

            # Request tools/list
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
            proc.stdin.flush()

            line2 = proc.stdout.readline()
            resp2 = json.loads(line2)
            self.assertEqual(resp2.get("id"), 2)
            tools = [t["name"] for t in resp2["result"]["tools"]]
            self.assertIn("get_context", tools)
            self.assertIn("search_history", tools)
            self.assertIn("get_turn", tools)
            self.assertIn("get_turns", tools)
            self.assertIn("search_patches", tools)
            self.assertIn("get_patch", tools)
            self.assertIn("list_tasks", tools)
            self.assertIn("list_skills", tools)
            print(f"Discovered {len(tools)} MCP tools over stdio successfully!")

        finally:
            proc.terminate()
            proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
