"""
Antigravity History Engine

Indexes and queries Antigravity conversation sessions:
  - ~/.gemini/antigravity-cli/conversation_summaries.db (global session metadata)
  - ~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl
  - ~/.gemini/antigravity-cli/brain/<uuid>/ (artifacts, task.md, implementation_plan.md, etc.)
  - Skills and Rules from ~/.gemini/config and ~/.claude/
"""
import glob
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ANTIGRAVITY_DATA_DIR = os.path.expanduser("~/.gemini/antigravity-cli")
BRAIN_DIR = os.path.join(ANTIGRAVITY_DATA_DIR, "brain")
SUMMARIES_DB_PATH = os.path.join(ANTIGRAVITY_DATA_DIR, "conversation_summaries.db")
GLOBAL_SKILLS_DIR = os.path.expanduser("~/.gemini/config/skills")
CLAUDE_SKILLS_DIR = os.path.expanduser("~/.claude/skills")
BUILTIN_SKILLS_DIR = os.path.join(ANTIGRAVITY_DATA_DIR, "builtin/skills")
GLOBAL_RULES_DIR = os.path.expanduser("~/.gemini/config/rules")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.db")


def _clean_workspace_path(uri_or_path: str) -> str:
    """Normalize file:// URI or filesystem path."""
    if not uri_or_path:
        return ""
    if uri_or_path.startswith("file://"):
        return uri_or_path[7:]
    return uri_or_path


def _extract_project_name(workspace_path: str) -> str:
    """Extract clean project name / slug from workspace path."""
    clean = _clean_workspace_path(workspace_path).rstrip("/")
    if not clean or clean == "outside-of-project":
        return "outside-of-project"
    return os.path.basename(clean) or clean


def _extract_task_items(markdown_content: str) -> List[Dict[str, Any]]:
    """Parse task.md markdown content for tasks, states, and IDs."""
    tasks = []
    pattern = re.compile(r"^(\s*)-\s*\[([ x/X~])\]\s*(.*?)(?:\s*<!--\s*id:\s*([^\s>]+)\s*-->)?\s*$", re.MULTILINE)
    for match in pattern.finditer(markdown_content):
        indent, mark, text, task_id = match.groups()
        mark_lower = mark.lower()
        if mark_lower == "x":
            status = "completed"
        elif mark_lower == "/":
            status = "in_progress"
        else:
            status = "pending"
        subject = text.strip()
        t_id = task_id.strip() if task_id else str(len(tasks))
        tasks.append({
            "task_id": t_id,
            "subject": subject,
            "status": status,
            "depth": len(indent) // 2
        })
    return tasks


class AntigravityHistoryEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.last_full_refresh = 0.0
        self._init_db()

    def get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        db = self.get_db()
        cur = db.cursor()

        # Cache state tracking for fast incremental sync
        cur.execute("""
            CREATE TABLE IF NOT EXISTS file_states (
                file_path TEXT PRIMARY KEY,
                mtime REAL,
                size INTEGER
            )
        """)

        # Sessions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workspace TEXT,
                project TEXT,
                title TEXT,
                preview TEXT,
                start_time TEXT,
                end_time TEXT,
                step_count INTEGER DEFAULT 0,
                patch_count INTEGER DEFAULT 0,
                task_count INTEGER DEFAULT 0,
                subagent_count INTEGER DEFAULT 0,
                last_prompt TEXT,
                status TEXT,
                app_data_dir TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time)")

        # Turns / Events table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                step_index INTEGER,
                timestamp TEXT,
                source TEXT,
                type TEXT,
                content TEXT,
                thinking TEXT,
                tool_calls TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                search_text TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_session_step ON events(session_id, step_index)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")

        # Snapshots (File mutations from write_file / edit_file)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                step_index INTEGER,
                patch_index INTEGER,
                timestamp TEXT,
                file_path TEXT,
                tool_name TEXT,
                action TEXT,
                target TEXT,
                replacement TEXT,
                content TEXT,
                size INTEGER,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_session_patch ON snapshots(session_id, patch_index)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_file_path ON snapshots(file_path)")

        # Tasks table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                task_id TEXT,
                subject TEXT,
                status TEXT,
                depth INTEGER DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

        # Subagents table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subagent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_session_id TEXT,
                child_session_id TEXT,
                step_index INTEGER,
                subagent_type TEXT,
                role TEXT,
                prompt TEXT,
                model TEXT,
                workspace TEXT,
                timestamp TEXT,
                FOREIGN KEY(parent_session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagent_runs(parent_session_id)")

        # Plans & Walkthroughs table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                plan_type TEXT,
                file_path TEXT,
                title TEXT,
                content TEXT,
                mtime REAL,
                size INTEGER,
                indexed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id)")

        # Artifacts table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                filename TEXT,
                file_path TEXT,
                summary TEXT,
                user_facing INTEGER DEFAULT 1,
                content TEXT,
                size INTEGER,
                mtime REAL,
                indexed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)")

        # Skills table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                source_type TEXT,
                file_path TEXT,
                description TEXT,
                content TEXT,
                mtime REAL,
                size INTEGER,
                indexed_at TEXT
            )
        """)

        # Rules table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                name TEXT PRIMARY KEY,
                source_type TEXT,
                file_path TEXT,
                content TEXT,
                mtime REAL,
                size INTEGER,
                indexed_at TEXT
            )
        """)

        db.commit()
        db.close()

    def query(self, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        db = self.get_db()
        try:
            cur = db.cursor()
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            db.close()

    def execute_mutating(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        db = self.get_db()
        try:
            cur = db.cursor()
            cur.execute(sql, params)
            db.commit()
            return cur.rowcount
        finally:
            db.close()

    # --------------------------------------------------------------------------
    # Sync / Refresh Pipeline
    # --------------------------------------------------------------------------

    def refresh(self, target_session_id: Optional[str] = None, force: bool = False) -> str:
        now = time.time()
        if target_session_id is None and not force:
            if now - self.last_full_refresh < 15:
                return "Cache fresh (cooldown)"
            self.last_full_refresh = now

        db = self.get_db()
        try:
            cur = db.cursor()
            cur.execute("SELECT file_path, mtime, size FROM file_states")
            file_states = {row["file_path"]: (row["mtime"], row["size"]) for row in cur.fetchall()}

            # 1. Sync global summaries
            self._sync_summaries(db, target_session_id)

            # 2. Sync brain directories (transcripts, tasks, plans, artifacts)
            self._sync_brains(db, file_states, target_session_id)

            # 3. Sync skills & rules (only on full refresh)
            if target_session_id is None:
                self._sync_skills(db, file_states)
                self._sync_rules(db, file_states)

            db.commit()
            return "Refreshed successfully"
        finally:
            db.close()

    def _sync_summaries(self, db: sqlite3.Connection, target_session_id: Optional[str]) -> None:
        """Read session metadata from conversation_summaries.db."""
        if not os.path.exists(SUMMARIES_DB_PATH):
            return

        src_conn = sqlite3.connect(SUMMARIES_DB_PATH)
        src_conn.row_factory = sqlite3.Row
        try:
            src_cur = src_conn.cursor()
            if target_session_id:
                src_cur.execute(
                    "SELECT conversation_id, title, preview, step_count, last_modified_time, "
                    "       workspace_uris, status, app_data_dir "
                    "FROM conversation_summaries WHERE conversation_id = ?",
                    (target_session_id,),
                )
            else:
                src_cur.execute(
                    "SELECT conversation_id, title, preview, step_count, last_modified_time, "
                    "       workspace_uris, status, app_data_dir "
                    "FROM conversation_summaries"
                )
            rows = src_cur.fetchall()
        finally:
            src_conn.close()

        cur = db.cursor()
        for r in rows:
            sid = r["conversation_id"]
            title = r["title"] or ""
            preview = r["preview"] or ""
            step_count = r["step_count"] or 0
            mod_time = r["last_modified_time"] or ""
            status = r["status"] or ""
            app_data_dir = r["app_data_dir"] or ""

            # Extract workspace & project
            workspace_uris = r["workspace_uris"] or "[]"
            try:
                uris = json.loads(workspace_uris)
                primary_uri = uris[0] if uris else ""
            except Exception:
                primary_uri = workspace_uris
            workspace = _clean_workspace_path(primary_uri)
            project = _extract_project_name(workspace)

            cur.execute("""
                INSERT INTO sessions (
                    session_id, workspace, project, title, preview, end_time,
                    step_count, status, app_data_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace = excluded.workspace,
                    project = excluded.project,
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE sessions.title END,
                    preview = CASE WHEN excluded.preview != '' THEN excluded.preview ELSE sessions.preview END,
                    end_time = excluded.end_time,
                    step_count = max(sessions.step_count, excluded.step_count),
                    status = excluded.status,
                    app_data_dir = excluded.app_data_dir
            """, (sid, workspace, project, title, preview, mod_time, step_count, status, app_data_dir))

    def _sync_brains(
        self,
        db: sqlite3.Connection,
        file_states: Dict[str, Tuple[float, int]],
        target_session_id: Optional[str]
    ) -> None:
        """Scan brain directories for transcripts, tasks, plans, and artifacts."""
        if not os.path.exists(BRAIN_DIR):
            return

        cur = db.cursor()
        brain_subdirs = [os.path.join(BRAIN_DIR, d) for d in os.listdir(BRAIN_DIR)]
        if target_session_id:
            brain_subdirs = [d for d in brain_subdirs if os.path.basename(d) == target_session_id]

        for b_dir in brain_subdirs:
            if not os.path.isdir(b_dir):
                continue
            sid = os.path.basename(b_dir)

            # Check transcript
            transcript_path = os.path.join(b_dir, ".system_generated/logs/transcript.jsonl")
            full_transcript_path = os.path.join(b_dir, ".system_generated/logs/transcript_full.jsonl")
            if os.path.exists(transcript_path):
                mtime = os.path.getmtime(transcript_path)
                size = os.path.getsize(transcript_path)
                cached = file_states.get(transcript_path)

                if cached is None or cached[0] != mtime or cached[1] != size:
                    self._index_transcript(db, sid, transcript_path, full_transcript_path)
                    cur.execute(
                        "INSERT INTO file_states (file_path, mtime, size) VALUES (?, ?, ?) "
                        "ON CONFLICT(file_path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                        (transcript_path, mtime, size)
                    )

            # Check task.md
            task_path = os.path.join(b_dir, "task.md")
            if os.path.exists(task_path):
                tmtime = os.path.getmtime(task_path)
                tsize = os.path.getsize(task_path)
                cached_task = file_states.get(task_path)
                if cached_task is None or cached_task[0] != tmtime or cached_task[1] != tsize:
                    self._index_task(db, sid, task_path)
                    cur.execute(
                        "INSERT INTO file_states (file_path, mtime, size) VALUES (?, ?, ?) "
                        "ON CONFLICT(file_path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                        (task_path, tmtime, tsize)
                    )

            # Check plans & artifacts (*.md in brain dir)
            for f in os.listdir(b_dir):
                if not f.endswith(".md") or f == "task.md":
                    continue
                md_path = os.path.join(b_dir, f)
                mmtime = os.path.getmtime(md_path)
                msize = os.path.getsize(md_path)
                cached_md = file_states.get(md_path)
                if cached_md is None or cached_md[0] != mmtime or cached_md[1] != msize:
                    self._index_markdown_artifact(db, sid, f, md_path)
                    cur.execute(
                        "INSERT INTO file_states (file_path, mtime, size) VALUES (?, ?, ?) "
                        "ON CONFLICT(file_path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                        (md_path, mmtime, msize)
                    )

    def _index_transcript(
        self,
        db: sqlite3.Connection,
        session_id: str,
        transcript_path: str,
        full_transcript_path: str
    ) -> None:
        """Parse transcript.jsonl and index events, snapshots, and subagents."""
        cur = db.cursor()

        # Check if full transcript exists for resolving truncated lines
        has_full = os.path.exists(full_transcript_path)

        # Remove existing events, snapshots, and subagents for this session
        cur.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
        cur.execute("DELETE FROM snapshots WHERE session_id = ?", (session_id,))
        cur.execute("DELETE FROM subagent_runs WHERE parent_session_id = ?", (session_id,))

        start_time = None
        end_time = None
        last_prompt = ""
        total_input_tokens = 0
        total_output_tokens = 0
        patch_index = 0
        subagent_count = 0
        step_count = 0

        # Read lines from transcript
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line_idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                step_idx = obj.get("step_index", line_idx)
                source = obj.get("source", "")
                stype = obj.get("type", "")
                ts = obj.get("created_at", "")
                content = obj.get("content", "") or ""
                thinking = obj.get("thinking", "") or ""
                inp_tokens = obj.get("input_tokens", 0) or 0
                out_tokens = obj.get("output_tokens", 0) or 0
                tool_calls = obj.get("tool_calls") or []

                if not start_time and ts:
                    start_time = ts
                if ts:
                    end_time = ts

                total_input_tokens += inp_tokens
                total_output_tokens += out_tokens
                step_count = max(step_count, step_idx + 1)

                # If truncated, optionally read untruncated line from transcript_full.jsonl
                if obj.get("truncated_fields") and has_full:
                    # Best effort: fetch exact line if manageable
                    pass

                # Track last user prompt
                if stype == "USER_INPUT" and source == "USER_EXPLICIT":
                    # Extract raw prompt inside <USER_REQUEST> if present
                    req_match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                    last_prompt = req_match.group(1).strip() if req_match else content.strip()

                # Process tool calls
                tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else ""
                search_parts = [content, thinking]

                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        tname = tc.get("name", "")
                        args = tc.get("args") or {}
                        # Args might be JSON string
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}

                        search_parts.append(tname)
                        for k, v in args.items():
                            if isinstance(v, str):
                                search_parts.append(v[:500])

                        # Snapshot detection
                        if tname == "write_file":
                            file_path = args.get("Path", "")
                            f_content = args.get("Content", "")
                            if isinstance(f_content, str):
                                is_ov = args.get("Overwrite", True)
                                is_app = args.get("Append", False)
                                action = "append" if is_app else ("overwrite" if is_ov else "create")
                                cur.execute("""
                                    INSERT INTO snapshots (
                                        session_id, step_index, patch_index, timestamp,
                                        file_path, tool_name, action, target, replacement,
                                        content, size
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    session_id, step_idx, patch_index, ts,
                                    file_path, tname, action, "", "",
                                    f_content, len(f_content)
                                ))
                                patch_index += 1

                        elif tname == "edit_file":
                            file_path = args.get("Path", "")
                            target = args.get("Target", "")
                            replacement = args.get("Replacement", "")
                            cur.execute("""
                                INSERT INTO snapshots (
                                    session_id, step_index, patch_index, timestamp,
                                    file_path, tool_name, action, target, replacement,
                                    content, size
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                session_id, step_idx, patch_index, ts,
                                file_path, tname, "edit", target, replacement,
                                replacement, len(replacement)
                            ))
                            patch_index += 1

                        elif tname == "invoke_subagent":
                            subagent_count += 1
                            sub_type = args.get("TypeName", "")
                            sub_role = args.get("Role", "")
                            sub_prompt = args.get("Prompt", "")
                            sub_model = args.get("Model", "")
                            sub_ws = args.get("Workspace", "")
                            cur.execute("""
                                INSERT INTO subagent_runs (
                                    parent_session_id, child_session_id, step_index,
                                    subagent_type, role, prompt, model, workspace, timestamp
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                session_id, "", step_idx,
                                sub_type, sub_role, sub_prompt, sub_model, sub_ws, ts
                            ))

                # Aggregate search text
                search_text = " ".join([p for p in search_parts if p])

                cur.execute("""
                    INSERT INTO events (
                        session_id, step_index, timestamp, source, type,
                        content, thinking, tool_calls, input_tokens, output_tokens, search_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id, step_idx, ts, source, stype,
                    content, thinking, tc_json, inp_tokens, out_tokens, search_text
                ))

        # Update session record with aggregated stats
        cur.execute("""
            UPDATE sessions SET
                start_time = COALESCE(start_time, ?),
                end_time = COALESCE(?, end_time),
                step_count = max(step_count, ?),
                patch_count = ?,
                subagent_count = ?,
                last_prompt = CASE WHEN ? != '' THEN ? ELSE last_prompt END,
                total_input_tokens = ?,
                total_output_tokens = ?
            WHERE session_id = ?
        """, (
            start_time, end_time, step_count, patch_index, subagent_count,
            last_prompt, last_prompt, total_input_tokens, total_output_tokens,
            session_id
        ))

    def _index_task(self, db: sqlite3.Connection, session_id: str, task_path: str) -> None:
        """Parse task.md and index into tasks table."""
        cur = db.cursor()
        cur.execute("DELETE FROM tasks WHERE session_id = ?", (session_id,))

        try:
            with open(task_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return

        items = _extract_task_items(content)
        for item in items:
            cur.execute("""
                INSERT INTO tasks (session_id, task_id, subject, status, depth)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, item["task_id"], item["subject"], item["status"], item["depth"]))

        cur.execute("UPDATE sessions SET task_count = ? WHERE session_id = ?", (len(items), session_id))

    def _index_markdown_artifact(
        self,
        db: sqlite3.Connection,
        session_id: str,
        filename: str,
        file_path: str
    ) -> None:
        """Index markdown files and sidecar metadata."""
        cur = db.cursor()
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return

        mtime = os.path.getmtime(file_path)
        size = len(content)
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))

        # Read sidecar .metadata.json if available
        summary = ""
        user_facing = 1
        meta_path = f"{file_path}.metadata.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                    summary = meta.get("Summary", "")
                    user_facing = 1 if meta.get("UserFacing", True) else 0
            except Exception:
                pass

        # Check if plan or general artifact
        if filename in ("implementation_plan.md", "walkthrough.md"):
            ptype = filename.replace(".md", "")
            first_line = content.split("\n", 1)[0].lstrip("#").strip() if content else filename
            cur.execute("DELETE FROM plans WHERE session_id = ? AND plan_type = ?", (session_id, ptype))
            cur.execute("""
                INSERT INTO plans (session_id, plan_type, file_path, title, content, mtime, size, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, ptype, file_path, first_line, content, mtime, size, now_iso))

        cur.execute("DELETE FROM artifacts WHERE session_id = ? AND filename = ?", (session_id, filename))
        cur.execute("""
            INSERT INTO artifacts (
                session_id, filename, file_path, summary, user_facing, content, size, mtime, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, filename, file_path, summary, user_facing, content, size, mtime, now_iso))

    def _sync_skills(self, db: sqlite3.Connection, file_states: Dict[str, Tuple[float, int]]) -> None:
        """Discover and index skills across global, claude, and builtin directories."""
        cur = db.cursor()
        sources = [
            ("gemini", GLOBAL_SKILLS_DIR),
            ("claude", CLAUDE_SKILLS_DIR),
            ("builtin", BUILTIN_SKILLS_DIR)
        ]

        for stype, sdir in sources:
            if not os.path.exists(sdir):
                continue
            for skill_dir in os.listdir(sdir):
                full_skill_dir = os.path.join(sdir, skill_dir)
                if not os.path.isdir(full_skill_dir):
                    continue
                skill_md = os.path.join(full_skill_dir, "SKILL.md")
                if not os.path.exists(skill_md):
                    continue

                mtime = os.path.getmtime(skill_md)
                size = os.path.getsize(skill_md)
                cached = file_states.get(skill_md)
                if cached is not None and cached[0] == mtime and cached[1] == size:
                    continue

                try:
                    with open(skill_md, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except Exception:
                    continue

                desc = ""
                name = skill_dir
                # Parse frontmatter
                if body.startswith("---"):
                    parts = body.split("---", 2)
                    if len(parts) >= 3:
                        header = parts[1]
                        for line in header.split("\n"):
                            if line.startswith("name:"):
                                name = line.replace("name:", "").strip()
                            elif line.startswith("description:"):
                                desc = line.replace("description:", "").strip()

                now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                cur.execute("""
                    INSERT INTO skills (name, source_type, file_path, description, content, mtime, size, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        source_type = excluded.source_type,
                        file_path = excluded.file_path,
                        description = excluded.description,
                        content = excluded.content,
                        mtime = excluded.mtime,
                        size = excluded.size,
                        indexed_at = excluded.indexed_at
                """, (name, stype, skill_md, desc, body, mtime, size, now_iso))

                cur.execute(
                    "INSERT INTO file_states (file_path, mtime, size) VALUES (?, ?, ?) "
                    "ON CONFLICT(file_path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                    (skill_md, mtime, size)
                )

    def _sync_rules(self, db: sqlite3.Connection, file_states: Dict[str, Tuple[float, int]]) -> None:
        """Discover and index global rules."""
        cur = db.cursor()
        if not os.path.exists(GLOBAL_RULES_DIR):
            return

        for fname in os.listdir(GLOBAL_RULES_DIR):
            if not fname.endswith(".md"):
                continue
            rpath = os.path.join(GLOBAL_RULES_DIR, fname)
            mtime = os.path.getmtime(rpath)
            size = os.path.getsize(rpath)
            cached = file_states.get(rpath)
            if cached is not None and cached[0] == mtime and cached[1] == size:
                continue

            try:
                with open(rpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            rname = fname.replace(".md", "")
            cur.execute("""
                INSERT INTO rules (name, source_type, file_path, content, mtime, size, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    source_type = excluded.source_type,
                    file_path = excluded.file_path,
                    content = excluded.content,
                    mtime = excluded.mtime,
                    size = excluded.size,
                    indexed_at = excluded.indexed_at
            """, (rname, "global", rpath, content, mtime, size, now_iso))

            cur.execute(
                "INSERT INTO file_states (file_path, mtime, size) VALUES (?, ?, ?) "
                "ON CONFLICT(file_path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                (rpath, mtime, size)
            )
