"""
Antigravity History MCP Server

Exposes Antigravity conversation history, turns, reasoning/thinking, code patches,
tasks, artifacts, and skills as searchable MCP tools and resources.
"""
import json
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from engine import AntigravityHistoryEngine

mcp = FastMCP("AntigravityHistory")
engine = AntigravityHistoryEngine()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _ok(payload: Any) -> str:
    """Format payload as fenced JSON block for agent-friendly parsing."""
    return f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```"


def _err(message: str, **extra: Any) -> str:
    """Format error as fenced JSON block."""
    return _ok({"error": message, **extra})


def _clip(s: Optional[str], n: int) -> str:
    """Clip text to n characters with length indicator."""
    if not s:
        return ""
    if n and len(s) > n:
        return s[:n] + f"…[{len(s)-n}]"
    return s


def _snippet(text: str, query: str, max_chars: int = 240) -> str:
    """Extract snippet window centered around matching query term."""
    if not text:
        return ""
    if not query or len(text) <= max_chars:
        return _clip(text, max_chars)

    tokens = [t.lower() for t in query.split() if t]
    pos = -1
    lower_text = text.lower()
    for tok in tokens:
        idx = lower_text.find(tok)
        if idx != -1:
            pos = idx
            break

    if pos == -1:
        return _clip(text, max_chars)

    start = max(0, pos - max_chars // 3)
    end = min(len(text), start + max_chars)
    snippet_str = text[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet_str}{suffix}"


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------

@mcp.resource("history://workspaces")
def list_workspaces_resource() -> str:
    """Distinct workspaces with Antigravity conversation sessions."""
    engine.refresh()
    rows = engine.query(
        "SELECT DISTINCT workspace, project, count(*) as session_count, max(end_time) as last_active "
        "FROM sessions WHERE workspace != '' GROUP BY workspace ORDER BY last_active DESC"
    )
    return _ok([dict(r) for r in rows])


@mcp.resource("history://sessions")
def list_recent_sessions_resource() -> str:
    """Recent Antigravity sessions across all workspaces."""
    engine.refresh()
    rows = engine.query(
        "SELECT session_id, project, workspace, title, step_count, patch_count, "
        "       start_time, end_time, last_prompt "
        "FROM sessions ORDER BY end_time DESC LIMIT 25"
    )
    return _ok([dict(r) for r in rows])


@mcp.resource("history://skills")
def list_skills_resource() -> str:
    """All registered skills across Antigravity, Claude, and builtins."""
    engine.refresh()
    rows = engine.query("SELECT name, source_type, file_path, description, size FROM skills ORDER BY name")
    return _ok([dict(r) for r in rows])


@mcp.resource("history://rules")
def list_rules_resource() -> str:
    """All registered rules."""
    engine.refresh()
    rows = engine.query("SELECT name, source_type, file_path, size FROM rules ORDER BY name")
    return _ok([dict(r) for r in rows])


# --------------------------------------------------------------------------
# Session & Context Tools
# --------------------------------------------------------------------------

@mcp.tool()
def get_context(limit: int = 5, workspace: Optional[str] = None) -> str:
    """Get the most recent Antigravity conversation sessions.

    Args:
        limit: Number of sessions to return (default 5).
        workspace: Optional workspace path filter.
    """
    try:
        engine.refresh()
        sql = (
            "SELECT session_id, project, workspace, title, preview, step_count, "
            "       patch_count, task_count, start_time, end_time, last_prompt "
            "FROM sessions "
        )
        params: List[Any] = []
        if workspace:
            sql += "WHERE workspace LIKE ? "
            params.append(f"%{workspace}%")
        sql += "ORDER BY end_time DESC LIMIT ?"
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        result = []
        for r in rows:
            result.append({
                "session_id": r["session_id"],
                "project": r["project"],
                "workspace": r["workspace"],
                "title": r["title"] or r["preview"][:60],
                "step_count": r["step_count"],
                "patch_count": r["patch_count"],
                "task_count": r["task_count"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "last_prompt": _clip(r["last_prompt"], 160)
            })
        return _ok({"count": len(result), "sessions": result})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_session_meta(session_id: str) -> str:
    """Get rich metadata for a specific Antigravity session.

    Includes workspace, times, step count, tokens, touched files, tasks,
    plans, artifacts, and subagents.
    """
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        if not rows:
            return _err("session_not_found", session_id=session_id)

        s = dict(rows[0])

        # Get files touched in this session
        file_rows = engine.query(
            "SELECT DISTINCT file_path, count(*) as edit_count, max(action) as last_action "
            "FROM snapshots WHERE session_id = ? GROUP BY file_path ORDER BY file_path",
            (session_id,)
        )
        files_touched = [dict(r) for r in file_rows]

        # Get tasks
        task_rows = engine.query(
            "SELECT task_id, subject, status FROM tasks WHERE session_id = ? ORDER BY id",
            (session_id,)
        )
        tasks = [dict(r) for r in task_rows]

        # Get plans & artifacts
        plan_rows = engine.query(
            "SELECT plan_type, title, size, file_path FROM plans WHERE session_id = ?",
            (session_id,)
        )
        plans = [dict(r) for r in plan_rows]

        artifact_rows = engine.query(
            "SELECT filename, summary, size, file_path FROM artifacts WHERE session_id = ?",
            (session_id,)
        )
        artifacts = [dict(r) for r in artifact_rows]

        # Get subagent runs
        sub_rows = engine.query(
            "SELECT subagent_type, role, prompt, model FROM subagent_runs WHERE parent_session_id = ?",
            (session_id,)
        )
        subagents = [dict(r) for r in sub_rows]

        return _ok({
            "session": s,
            "files_touched_count": len(files_touched),
            "files_touched": files_touched,
            "tasks_count": len(tasks),
            "tasks": tasks,
            "plans": plans,
            "artifacts": artifacts,
            "subagents": subagents
        })
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


# --------------------------------------------------------------------------
# Conversation Turns & History Search
# --------------------------------------------------------------------------

@mcp.tool()
def search_history(
    query: str,
    workspaces: Optional[List[str]] = None,
    projects: Optional[List[str]] = None,
    session_ids: Optional[List[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    limit: int = 10,
    preview_chars: int = 240
) -> str:
    """Search across Antigravity conversation turns (user requests, model responses, reasoning, tool calls).

    Args:
        query: Search keywords or phrase.
        workspaces: Optional list of workspace substrings to filter by.
        projects: Optional list of project slugs.
        session_ids: Optional list of session IDs.
        since: ISO 8601 timestamp minimum.
        until: ISO 8601 timestamp maximum.
        kinds: Turn types to include: user, assistant, thinking, tool_call, tool_result.
        limit: Max results (default 10).
        preview_chars: Preview length for snippet (default 240).
    """
    try:
        engine.refresh()
        tokens = [t.strip() for t in query.split() if t.strip()]
        if not tokens:
            return _err("empty_query")

        clauses = []
        params: List[Any] = []

        # Match tokens against search_text
        for tok in tokens:
            clauses.append("e.search_text LIKE ?")
            params.append(f"%{tok}%")

        if session_ids:
            ph = ",".join(["?"] * len(session_ids))
            clauses.append(f"e.session_id IN ({ph})")
            params.extend(session_ids)

        if projects:
            p_clauses = ["s.project LIKE ?" for _ in projects]
            clauses.append(f"({' OR '.join(p_clauses)})")
            params.extend([f"%{p}%" for p in projects])

        if workspaces:
            w_clauses = ["s.workspace LIKE ?" for _ in workspaces]
            clauses.append(f"({' OR '.join(w_clauses)})")
            params.extend([f"%{w}%" for w in workspaces])

        if since:
            clauses.append("e.timestamp >= ?")
            params.append(since)

        if until:
            clauses.append("e.timestamp <= ?")
            params.append(until)

        if kinds:
            k_clauses = []
            for k in kinds:
                kl = k.lower()
                if kl == "user":
                    k_clauses.append("(e.type = 'USER_INPUT')")
                elif kl == "assistant":
                    k_clauses.append("(e.type = 'PLANNER_RESPONSE')")
                elif kl == "thinking":
                    k_clauses.append("(e.thinking != '')")
                elif kl == "tool_call":
                    k_clauses.append("(e.tool_calls != '' AND e.tool_calls != '[]')")
                elif kl == "tool_result":
                    k_clauses.append("(e.type = 'GENERIC')")
            if k_clauses:
                clauses.append(f"({' OR '.join(k_clauses)})")

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT e.session_id, s.project, s.workspace, e.step_index, e.timestamp,
                   e.source, e.type, e.content, e.thinking, e.tool_calls
            FROM events e
            JOIN sessions s ON e.session_id = s.session_id
            WHERE {where_sql}
            ORDER BY e.timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        hits = []
        for r in rows:
            content = r["content"] or ""
            thinking = r["thinking"] or ""
            preview_source = content if content else thinking
            hits.append({
                "session_id": r["session_id"],
                "project": r["project"],
                "workspace": r["workspace"],
                "step_index": r["step_index"],
                "timestamp": r["timestamp"],
                "source": r["source"],
                "type": r["type"],
                "has_thinking": bool(thinking),
                "has_tool_calls": bool(r["tool_calls"]),
                "preview": _snippet(preview_source, query, preview_chars)
            })

        return _ok({"count": len(hits), "hits": hits})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_turn(
    session_id: str,
    step_index: int,
    include_thinking: bool = False,
    include_tool_results: bool = False,
    max_chars: int = 4000
) -> str:
    """Fetch one specific conversation turn by (session_id, step_index).

    Args:
        session_id: Target session ID.
        step_index: Integer step index.
        include_thinking: Include model chain-of-thought (thinking).
        include_tool_results: If true and turn called tools, includes tool execution result.
        max_chars: Character truncation limit (default 4000).
    """
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT * FROM events WHERE session_id = ? AND step_index = ?",
            (session_id, step_index)
        )
        if not rows:
            return _err("turn_not_found", session_id=session_id, step_index=step_index)

        r = dict(rows[0])
        tool_calls = json.loads(r["tool_calls"]) if r["tool_calls"] else []

        payload = {
            "session_id": r["session_id"],
            "step_index": r["step_index"],
            "timestamp": r["timestamp"],
            "source": r["source"],
            "type": r["type"],
            "content": _clip(r["content"], max_chars),
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"]
        }

        if include_thinking and r["thinking"]:
            payload["thinking"] = _clip(r["thinking"], max_chars)

        if tool_calls:
            payload["tool_calls"] = tool_calls

        if include_tool_results and r["type"] == "PLANNER_RESPONSE":
            # Find next GENERIC turn(s) for results
            next_rows = engine.query(
                "SELECT step_index, content FROM events "
                "WHERE session_id = ? AND step_index > ? AND type = 'GENERIC' "
                "ORDER BY step_index ASC LIMIT 2",
                (session_id, step_index)
            )
            if next_rows:
                payload["tool_results"] = [
                    {"step_index": nr["step_index"], "result": _clip(nr["content"], max_chars)}
                    for nr in next_rows
                ]

        return _ok(payload)
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_turns(
    session_id: str,
    start_index: Optional[int] = None,
    end_index: Optional[int] = None,
    kinds: Optional[List[str]] = None,
    include_thinking: bool = False,
    include_tool_results: bool = False,
    limit: int = 20,
    max_chars_per_turn: int = 1000
) -> str:
    """Fetch a slice or range of conversation turns from a session.

    Supports negative start_index (e.g. start_index=-5 pulls the last 5 turns).
    """
    try:
        engine.refresh(target_session_id=session_id)
        # Check max step index for negative offset calculation
        max_row = engine.query("SELECT max(step_index) as m FROM events WHERE session_id = ?", (session_id,))
        if not max_row or max_row[0]["m"] is None:
            return _err("session_empty_or_not_found", session_id=session_id)

        max_idx = max_row[0]["m"]
        real_start = 0
        real_end = max_idx

        if start_index is not None:
            real_start = (max_idx + 1 + start_index) if start_index < 0 else start_index
        if end_index is not None:
            real_end = (max_idx + 1 + end_index) if end_index < 0 else end_index

        clauses = ["session_id = ?", "step_index >= ?", "step_index <= ?"]
        params: List[Any] = [session_id, real_start, real_end]

        if kinds:
            k_clauses = []
            for k in kinds:
                kl = k.lower()
                if kl == "user":
                    k_clauses.append("(type = 'USER_INPUT')")
                elif kl == "assistant":
                    k_clauses.append("(type = 'PLANNER_RESPONSE')")
                elif kl == "tool_result":
                    k_clauses.append("(type = 'GENERIC')")
            if k_clauses:
                clauses.append(f"({' OR '.join(k_clauses)})")

        sql = f"""
            SELECT step_index, timestamp, source, type, content, thinking, tool_calls
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY step_index ASC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        turns = []
        for r in rows:
            turn = {
                "step_index": r["step_index"],
                "timestamp": r["timestamp"],
                "source": r["source"],
                "type": r["type"],
                "content": _clip(r["content"], max_chars_per_turn)
            }
            if include_thinking and r["thinking"]:
                turn["thinking"] = _clip(r["thinking"], max_chars_per_turn)
            if r["tool_calls"]:
                try:
                    tc = json.loads(r["tool_calls"])
                    turn["tool_names"] = [item.get("name") for item in tc if isinstance(item, dict)]
                except Exception:
                    pass
            turns.append(turn)

        return _ok({
            "session_id": session_id,
            "range": [real_start, real_end],
            "count": len(turns),
            "turns": turns
        })
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


# --------------------------------------------------------------------------
# Patches & File Snapshots
# --------------------------------------------------------------------------

@mcp.tool()
def search_patches(
    query: str,
    workspaces: Optional[List[str]] = None,
    session_ids: Optional[List[str]] = None,
    file_path_pattern: Optional[str] = None,
    patch_index: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 10,
    preview_chars: int = 240
) -> str:
    """Search file mutations (write_file, edit_file) across sessions.

    Args:
        query: Keyword to search inside modified file content or diff.
        workspaces: Workspace substring filters.
        session_ids: Session IDs list.
        file_path_pattern: SQL LIKE pattern for file path (e.g. '%.py' or '%server%').
        patch_index: Specific patch index.
        action: Filter by action (create, overwrite, append, edit).
        limit: Max results (default 10).
        preview_chars: Preview length for content snippet.
    """
    try:
        engine.refresh()
        clauses = []
        params: List[Any] = []

        tokens = [t.strip() for t in query.split() if t.strip()]
        for tok in tokens:
            clauses.append("(sn.content LIKE ? OR sn.file_path LIKE ?)")
            params.extend([f"%{tok}%", f"%{tok}%"])

        if session_ids:
            ph = ",".join(["?"] * len(session_ids))
            clauses.append(f"sn.session_id IN ({ph})")
            params.extend(session_ids)

        if file_path_pattern:
            clauses.append("sn.file_path LIKE ?")
            params.append(file_path_pattern)

        if patch_index is not None:
            clauses.append("sn.patch_index = ?")
            params.append(patch_index)

        if action:
            clauses.append("sn.action = ?")
            params.append(action)

        if workspaces:
            w_clauses = ["s.workspace LIKE ?" for _ in workspaces]
            clauses.append(f"({' OR '.join(w_clauses)})")
            params.extend([f"%{w}%" for w in workspaces])

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT sn.session_id, s.project, s.workspace, sn.step_index, sn.patch_index,
                   sn.timestamp, sn.file_path, sn.tool_name, sn.action, sn.size, sn.content
            FROM snapshots sn
            JOIN sessions s ON sn.session_id = s.session_id
            {where_sql}
            ORDER BY sn.timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        hits = []
        for r in rows:
            hits.append({
                "session_id": r["session_id"],
                "project": r["project"],
                "patch_index": r["patch_index"],
                "step_index": r["step_index"],
                "timestamp": r["timestamp"],
                "file_path": r["file_path"],
                "tool_name": r["tool_name"],
                "action": r["action"],
                "size": r["size"],
                "preview": _snippet(r["content"] or "", query, preview_chars)
            })

        return _ok({"count": len(hits), "hits": hits})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_patch(session_id: str, patch_index: int) -> str:
    """Get the full content or diff of a single file snapshot/patch.

    Args:
        session_id: Target session ID.
        patch_index: Sequential patch index.
    """
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT * FROM snapshots WHERE session_id = ? AND patch_index = ?",
            (session_id, patch_index)
        )
        if not rows:
            return _err("patch_not_found", session_id=session_id, patch_index=patch_index)

        r = dict(rows[0])
        return _ok(r)
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_patches(
    session_ids: Optional[List[str]] = None,
    file_path_pattern: Optional[str] = None,
    limit: int = 50
) -> str:
    """Discovery tool: lists the latest patch per file touched across sessions."""
    try:
        engine.refresh()
        clauses = []
        params: List[Any] = []

        if session_ids:
            ph = ",".join(["?"] * len(session_ids))
            clauses.append(f"session_id IN ({ph})")
            params.extend(session_ids)

        if file_path_pattern:
            clauses.append("file_path LIKE ?")
            params.append(file_path_pattern)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT session_id, file_path, max(patch_index) as latest_patch_index,
                   count(*) as total_patches, max(timestamp) as last_modified,
                   max(size) as latest_size
            FROM snapshots
            {where_sql}
            GROUP BY session_id, file_path
            ORDER BY last_modified DESC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        return _ok({"count": len(rows), "files": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_file_version(
    session_id: str,
    file_path: str,
    patch_index: Optional[int] = None,
    step_index: Optional[int] = None
) -> str:
    """Retrieve file content at a given patch or step within a session."""
    try:
        engine.refresh(target_session_id=session_id)
        if patch_index is not None:
            rows = engine.query(
                "SELECT * FROM snapshots WHERE session_id = ? AND file_path = ? AND patch_index = ?",
                (session_id, file_path, patch_index)
            )
        elif step_index is not None:
            rows = engine.query(
                "SELECT * FROM snapshots WHERE session_id = ? AND file_path = ? AND step_index <= ? "
                "ORDER BY step_index DESC LIMIT 1",
                (session_id, file_path, step_index)
            )
        else:
            rows = engine.query(
                "SELECT * FROM snapshots WHERE session_id = ? AND file_path = ? "
                "ORDER BY patch_index DESC LIMIT 1",
                (session_id, file_path)
            )

        if not rows:
            return _err("file_version_not_found", session_id=session_id, file_path=file_path)

        return _ok(dict(rows[0]))
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_file_history(session_id: str, file_path: str) -> str:
    """Lifecycle of one file in a session: all patches that modified it."""
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT patch_index, step_index, timestamp, tool_name, action, size "
            "FROM snapshots WHERE session_id = ? AND file_path = ? ORDER BY patch_index ASC",
            (session_id, file_path)
        )
        return _ok({
            "session_id": session_id,
            "file_path": file_path,
            "mutation_count": len(rows),
            "history": [dict(r) for r in rows]
        })
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def list_files_in_session(session_id: str, limit: int = 100) -> str:
    """List all unique files touched/modified in a session."""
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT file_path, count(*) as edits, min(timestamp) as first_edit, "
            "       max(timestamp) as last_edit, max(size) as last_size "
            "FROM snapshots WHERE session_id = ? GROUP BY file_path ORDER BY last_edit DESC LIMIT ?",
            (session_id, limit)
        )
        return _ok({"session_id": session_id, "count": len(rows), "files": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


# --------------------------------------------------------------------------
# Tasks, Subagents, Plans, Artifacts
# --------------------------------------------------------------------------

@mcp.tool()
def list_tasks(
    status: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50
) -> str:
    """List tasks extracted from task.md across sessions or for a specific session.

    Args:
        status: Filter by 'pending', 'in_progress', or 'completed'.
        session_id: Filter by session ID.
        limit: Max rows (default 50).
    """
    try:
        engine.refresh(target_session_id=session_id)
        clauses = []
        params: List[Any] = []

        if status:
            clauses.append("t.status = ?")
            params.append(status.lower())

        if session_id:
            clauses.append("t.session_id = ?")
            params.append(session_id)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT t.session_id, s.project, s.workspace, t.task_id, t.subject, t.status, t.depth
            FROM tasks t
            JOIN sessions s ON t.session_id = s.session_id
            {where_sql}
            ORDER BY t.id DESC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        return _ok({"count": len(rows), "tasks": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def list_subagents(
    parent_session_id: Optional[str] = None,
    subagent_type: Optional[str] = None,
    limit: int = 50
) -> str:
    """List subagents launched via invoke_subagent across sessions."""
    try:
        engine.refresh(target_session_id=parent_session_id)
        clauses = []
        params: List[Any] = []

        if parent_session_id:
            clauses.append("parent_session_id = ?")
            params.append(parent_session_id)

        if subagent_type:
            clauses.append("subagent_type LIKE ?")
            params.append(f"%{subagent_type}%")

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM subagent_runs {where_sql} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        return _ok({"count": len(rows), "subagents": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def search_plans(query: str, limit: int = 10) -> str:
    """Search implementation plans and walkthroughs in brain directories."""
    try:
        engine.refresh()
        tokens = [t.strip() for t in query.split() if t.strip()]
        if not tokens:
            return _err("empty_query")

        clauses = ["(content LIKE ? OR title LIKE ?)" for _ in tokens]
        params = []
        for tok in tokens:
            params.extend([f"%{tok}%", f"%{tok}%"])

        sql = f"""
            SELECT p.session_id, s.project, s.workspace, p.plan_type, p.title,
                   p.size, p.file_path, p.indexed_at, p.content
            FROM plans p
            JOIN sessions s ON p.session_id = s.session_id
            WHERE {' AND '.join(clauses)}
            ORDER BY p.mtime DESC
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        hits = []
        for r in rows:
            hits.append({
                "session_id": r["session_id"],
                "project": r["project"],
                "plan_type": r["plan_type"],
                "title": r["title"],
                "size": r["size"],
                "file_path": r["file_path"],
                "preview": _snippet(r["content"], query, 240)
            })
        return _ok({"count": len(hits), "plans": hits})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_plan(session_id: str, plan_type: str = "implementation_plan") -> str:
    """Get the full body of an implementation plan or walkthrough by session ID.

    Args:
        session_id: Target session ID.
        plan_type: 'implementation_plan' or 'walkthrough'.
    """
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT * FROM plans WHERE session_id = ? AND plan_type LIKE ?",
            (session_id, f"%{plan_type}%")
        )
        if not rows:
            return _err("plan_not_found", session_id=session_id, plan_type=plan_type)

        r = dict(rows[0])
        return _ok(r)
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def list_artifacts(session_id: Optional[str] = None, limit: int = 50) -> str:
    """List markdown artifacts created in brain directories."""
    try:
        engine.refresh(target_session_id=session_id)
        if session_id:
            rows = engine.query(
                "SELECT session_id, filename, summary, user_facing, size, mtime, file_path "
                "FROM artifacts WHERE session_id = ? ORDER BY mtime DESC LIMIT ?",
                (session_id, limit)
            )
        else:
            rows = engine.query(
                "SELECT session_id, filename, summary, user_facing, size, mtime, file_path "
                "FROM artifacts ORDER BY mtime DESC LIMIT ?",
                (limit,)
            )
        return _ok({"count": len(rows), "artifacts": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_artifact(session_id: str, filename: str) -> str:
    """Get full body and metadata of a specific brain artifact."""
    try:
        engine.refresh(target_session_id=session_id)
        rows = engine.query(
            "SELECT * FROM artifacts WHERE session_id = ? AND filename = ?",
            (session_id, filename)
        )
        if not rows:
            return _err("artifact_not_found", session_id=session_id, filename=filename)

        return _ok(dict(rows[0]))
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


# --------------------------------------------------------------------------
# Customizations: Skills & Rules
# --------------------------------------------------------------------------

@mcp.tool()
def list_skills(source_type: Optional[str] = None) -> str:
    """List all registered skills (Gemini, Claude, Builtin)."""
    try:
        engine.refresh()
        if source_type:
            rows = engine.query(
                "SELECT name, source_type, description, file_path, size, mtime "
                "FROM skills WHERE source_type = ? ORDER BY name",
                (source_type,)
            )
        else:
            rows = engine.query(
                "SELECT name, source_type, description, file_path, size, mtime "
                "FROM skills ORDER BY name"
            )
        return _ok({"count": len(rows), "skills": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def search_skills(query: str, limit: int = 5) -> str:
    """Fulltext search across skill descriptions and SKILL.md contents."""
    try:
        engine.refresh()
        tokens = [t.strip() for t in query.split() if t.strip()]
        if not tokens:
            return _err("empty_query")

        clauses = ["(content LIKE ? OR description LIKE ? OR name LIKE ?)" for _ in tokens]
        params = []
        for tok in tokens:
            params.extend([f"%{tok}%", f"%{tok}%", f"%{tok}%"])

        sql = f"""
            SELECT name, source_type, description, file_path, size, content
            FROM skills
            WHERE {' AND '.join(clauses)}
            ORDER BY name
            LIMIT ?
        """
        params.append(limit)

        rows = engine.query(sql, tuple(params))
        hits = []
        for r in rows:
            hits.append({
                "name": r["name"],
                "source_type": r["source_type"],
                "description": r["description"],
                "file_path": r["file_path"],
                "size": r["size"],
                "preview": _snippet(r["content"], query, 240)
            })
        return _ok({"count": len(hits), "skills": hits})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_skill(name: str) -> str:
    """Get the full content of a skill (SKILL.md)."""
    try:
        engine.refresh()
        rows = engine.query("SELECT * FROM skills WHERE name = ?", (name,))
        if not rows:
            return _err("skill_not_found", name=name)

        return _ok(dict(rows[0]))
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def list_rules(source_type: Optional[str] = None) -> str:
    """List all registered system and project rules."""
    try:
        engine.refresh()
        if source_type:
            rows = engine.query(
                "SELECT name, source_type, file_path, size, mtime FROM rules WHERE source_type = ? ORDER BY name",
                (source_type,)
            )
        else:
            rows = engine.query(
                "SELECT name, source_type, file_path, size, mtime FROM rules ORDER BY name"
            )
        return _ok({"count": len(rows), "rules": [dict(r) for r in rows]})
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


@mcp.tool()
def get_rule(name: str) -> str:
    """Get the full markdown content of a rule."""
    try:
        engine.refresh()
        rows = engine.query("SELECT * FROM rules WHERE name = ?", (name,))
        if not rows:
            return _err("rule_not_found", name=name)

        return _ok(dict(rows[0]))
    except Exception as e:
        return _err(str(e), trace=traceback.format_exc())


# --------------------------------------------------------------------------
# Main Stdio Entrypoint
# --------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
