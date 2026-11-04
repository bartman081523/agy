# Antigravity History MCP

MCP server exposing Antigravity conversation history, turns, reasoning/thinking, code patches,
tasks, plans, artifacts, and skills as searchable resources and tools.

Modeled after and with full functional parity to `claude-history-mcp`.

## Architecture & Data Sources

Reads native Antigravity structures directly:
- `~/.gemini/antigravity-cli/conversation_summaries.db`: Global session metadata, titles, workspaces, modification times.
- `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`: Conversation turns, prompts, model thinking, tool calls, and tool outputs.
- `~/.gemini/antigravity-cli/brain/<uuid>/`: Artifacts, `task.md`, `implementation_plan.md`, `walkthrough.md`.
- `~/.gemini/config/skills/`, `~/.claude/skills/`, `~/.gemini/antigravity-cli/builtin/skills/`: Registered skills.
- `~/.gemini/config/rules/`, `.agents/rules/`: System and project rules.

Data is indexed into a local SQLite database (`history.db`) with WAL mode, foreign keys, and fast incremental updates (15s cooldown, mtime/size checks).

## Design Principles

1. **Fenced JSON blocks**: Every tool returns a fenced ```json ... ``` block for immediate and error-free parsing by AI agents.
2. **Stable Handles**: Every record carries stable, citable handles: `session_id`, `step_index`, `patch_index`, `file_path`, `timestamp`.
3. **Tight Defaults**: Discovery tools (`search_history`, `search_patches`) return handles and previews, not full message bodies, preserving the agent's context window.
4. **Pure Local Execution**: Zero external model calls — all operations run directly against local SQLite.

## Tools (22 Tools)

### Session & Context
| Tool | Description |
|---|---|
| `get_context(limit=5, workspace=None)` | Most-recent sessions across all workspaces (id, project, workspace, title, step count, patch count, times). |
| `get_session_meta(session_id)` | One-shot deep description of a session: workspace, tokens, step count, files touched, tasks, plans, artifacts, subagents. |

### Conversation Turns & Search
| Tool | Description |
|---|---|
| `search_history(query, workspaces=None, projects=None, session_ids=None, since=None, until=None, kinds=None, limit=10, preview_chars=240)` | Multi-token search across turns (user requests, model responses, reasoning, tool calls). |
| `get_turn(session_id, step_index, include_thinking=False, include_tool_results=False, max_chars=4000)` | Retrieve a specific turn. Optional inclusion of model thinking (Chain-of-Thought) and tool execution results. |
| `get_turns(session_id, start_index=None, end_index=None, kinds=None, include_thinking=False, include_tool_results=False, limit=20, max_chars_per_turn=1000)` | Fetch a slice of turns. Supports negative offsets (e.g. `start_index=-5` for the last 5 turns). |

### Patches & Code Changes
| Tool | Description |
|---|---|
| `search_patches(query, workspaces=None, session_ids=None, file_path_pattern=None, patch_index=None, action=None, limit=10, preview_chars=240)` | Search file modifications (`write_file`, `edit_file`) across sessions. |
| `get_patch(session_id, patch_index)` | Full body, replacement, or diff of a single file modification. |
| `get_patches(session_ids=None, file_path_pattern=None, limit=50)` | Discovery tool: lists the latest patch per file touched across sessions. |
| `get_file_version(session_id, file_path, patch_index=None, step_index=None)` | Retrieve file content at a given patch or step within a session. |
| `get_file_history(session_id, file_path)` | Lifecycle of a file in a session (every patch that modified it with action, size, timestamp). |
| `list_files_in_session(session_id, limit=100)` | All unique files touched/modified in a session. |

### Tasks, Subagents, Plans & Artifacts
| Tool | Description |
|---|---|
| `list_tasks(status=None, session_id=None, limit=50)` | Tasks extracted from `task.md` across sessions (pending, in_progress, completed). |
| `list_subagents(parent_session_id=None, subagent_type=None, limit=50)` | Subagents launched via `invoke_subagent` across sessions. |
| `search_plans(query, limit=10)` | Search implementation plans and walkthroughs in brain directories. |
| `get_plan(session_id, plan_type="implementation_plan")` | Full body of an implementation plan or walkthrough by session ID. |
| `list_artifacts(session_id=None, limit=50)` | List markdown artifacts created in brain directories with metadata. |
| `get_artifact(session_id, filename)` | Full body and metadata of a specific brain artifact. |

### Customizations: Skills & Rules
| Tool | Description |
|---|---|
| `list_skills(source_type=None)` | List all registered skills (Gemini, Claude, Builtin). |
| `search_skills(query, limit=5)` | Fulltext search across skill descriptions and SKILL.md contents. |
| `get_skill(name)` | Full content of a skill (`SKILL.md`). |
| `list_rules(source_type=None)` | List all registered system and project rules. |
| `get_rule(name)` | Full markdown content of a rule. |

## Resources

- `history://workspaces`: Distinct workspaces and active session counts.
- `history://sessions`: Recent sessions list.
- `history://skills`: Registered skills index.
- `history://rules`: Registered rules index.

## Configuration

### Antigravity (`~/.gemini/config/mcp_config.json`)
```json
{
  "mcpServers": {
    "antigravity-history": {
      "command": "/run/media/julian/ML6/agy/antigravity-history-mcp/run_server.sh"
    }
  }
}
```

### Claude Code (`~/.claude.json`)
```json
{
  "mcpServers": {
    "antigravity-history": {
      "command": "/run/media/julian/ML6/agy/antigravity-history-mcp/run_server.sh",
      "transport": "stdio"
    }
  }
}
```

## Running Tests

```bash
cd /run/media/julian/ML6/agy/antigravity-history-mcp
./venv/bin/python3 -m unittest discover -s tests
```
