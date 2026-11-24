"""Infrastructure Module: Context Management & History Persistence
Ported from gemini-cli feat/context-management (commits 060ded8a4, f4b823629).
"""
import os
import json
import sqlite3
from typing import List, Dict, Any, Optional

HISTORY_DB_PATH = os.environ.get(
    "CLAUDE_HISTORY_DB",
    os.path.expanduser("~/.claude-history/history.db")
)

class ContextManager:
    """Manages session context, reselection, and persistence for AGY."""

    def __init__(self, db_path: str = HISTORY_DB_PATH):
        self.db_path = db_path

    def list_recent_sessions(self, limit: int = 10, project: Optional[str] = None) -> List[Dict[str, Any]]:
        """List recent sessions from the persistent store."""
        if not os.path.exists(self.db_path):
            return []
        
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if project:
                cursor.execute(
                    "SELECT session_id, project, start_time, end_time, summary, patch_count "
                    "FROM sessions WHERE project LIKE ? ORDER BY start_time DESC LIMIT ?",
                    (f"%{project}%", limit)
                )
            else:
                cursor.execute(
                    "SELECT session_id, project, start_time, end_time, summary, patch_count "
                    "FROM sessions ORDER BY start_time DESC LIMIT ?",
                    (limit,)
                )
            
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception as e:
            return [{"error": str(e)}]

    def reselect_context(self, session_id: str) -> Dict[str, Any]:
        """Reselect and reconstruct conversation context from a previous session."""
        if not os.path.exists(self.db_path):
            return {"error": "Database not found"}

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT session_id, project, summary, last_prompt, cwd FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            session = cursor.fetchone()
            if not session:
                conn.close()
                return {"error": f"Session {session_id} not found"}

            cursor.execute(
                "SELECT file_path, patch_index, timestamp, content FROM snapshots WHERE session_id = ? ORDER BY patch_index ASC",
                (session_id,)
            )
            snapshots = [dict(row) for row in cursor.fetchall()]
            conn.close()

            return {
                "session": dict(session),
                "snapshot_count": len(snapshots),
                "snapshots": snapshots
            }
        except Exception as e:
            return {"error": str(e)}
