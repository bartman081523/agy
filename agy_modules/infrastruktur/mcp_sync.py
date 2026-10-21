"""Infrastructure Module: Claude Code MCP Syncer
Automatically syncs Claude Code MCP servers (claude-history, rea) into Antigravity configurations.
"""
import os
import json
from typing import Dict, Any

CLAUDE_CONFIG_PATH = os.path.expanduser("~/.claude.json")
AGY_GLOBAL_MCP_PATH = os.path.expanduser("~/.gemini/config/mcp_config.json")

def sync_mcp_servers(target_path: str = AGY_GLOBAL_MCP_PATH) -> Dict[str, Any]:
    """Sync mcpServers section from ~/.claude.json into target mcp_config.json."""
    if not os.path.exists(CLAUDE_CONFIG_PATH):
        return {"status": "skipped", "reason": "No ~/.claude.json"}

    try:
        with open(CLAUDE_CONFIG_PATH, "r", encoding="utf-8") as f:
            claude_data = json.load(f)
        
        claude_mcps = claude_data.get("mcpServers", {})
        if not claude_mcps:
            return {"status": "skipped", "reason": "No mcpServers in ~/.claude.json"}

        # Prepare normalized AGY MCP dictionary
        agy_mcps = {}
        for name, spec in claude_mcps.items():
            normalized = {}
            if "command" in spec:
                normalized["command"] = spec["command"]
            if "args" in spec:
                normalized["args"] = spec["args"]
            if "env" in spec:
                normalized["env"] = spec["env"]
            if "url" in spec:
                normalized["url"] = spec["url"]
            agy_mcps[name] = normalized

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        current = {}
        if os.path.exists(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}

        current.setdefault("mcpServers", {})
        current["mcpServers"].update(agy_mcps)

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)

        return {
            "status": "synced",
            "servers": list(agy_mcps.keys()),
            "target": target_path
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

if __name__ == "__main__":
    res = sync_mcp_servers()
    print(json.dumps(res, indent=2))
