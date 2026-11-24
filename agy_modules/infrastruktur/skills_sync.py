"""Infrastructure Module: Claude Code & Antigravity Skills Synchronizer
Agnostically synchronizes skills between Claude Code (~/.claude/skills) and Antigravity,
normalizing frontmatter to comply strictly with Antigravity's YAML requirements so that
all skills (e.g. /metaorchestratormind, /devmind, etc.) are available as dynamic slash commands.
"""
import os
import sys
import json
import re
from typing import Dict, Any, Tuple, Optional
import yaml

DEFAULT_CLAUDE_SKILLS = os.path.expanduser("~/.claude/skills")
DEFAULT_GEMINI_CONFIG = os.path.expanduser("~/.gemini/config")


def normalize_skill_frontmatter(content: str, folder_name: str) -> Tuple[str, bool]:
    """Normalize SKILL.md content to ensure strict, valid YAML frontmatter.

    Returns:
        (normalized_content, was_modified)
    """
    # Case 1: Missing YAML frontmatter completely
    if not content.startswith("---"):
        lines = content.strip().splitlines()
        name = folder_name
        desc_paragraphs = []
        collecting = False
        for line in lines:
            if line.startswith("#"):
                continue
            if line.strip():
                collecting = True
                desc_paragraphs.append(line.strip())
            elif collecting:
                break
        desc = " ".join(desc_paragraphs) if desc_paragraphs else f"Skill {name}"
        desc = re.sub(r"[*_`]", "", desc).strip()
        if len(desc) > 350:
            desc = desc[:347] + "..."
        fm_dict = {"name": name, "description": desc}
        dumped = yaml.dump(fm_dict, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{dumped}\n---\n\n{content.lstrip()}", True

    # Case 2: Frontmatter exists - verify whether YAML parses cleanly
    parts = content.split("---", 2)
    if len(parts) < 3:
        # Broken frontmatter delimiter
        return content, False

    raw_fm = parts[1]
    body = parts[2]

    try:
        data = yaml.safe_load(raw_fm)
        if isinstance(data, dict) and "name" in data and "description" in data:
            # Check if name is valid string and description is valid string
            if isinstance(data["name"], str) and isinstance(data["description"], str):
                return content, False
    except Exception:
        pass

    # Extract name and description manually from raw frontmatter
    name = folder_name
    m_name = re.search(r"^name:\s*(.+)$", raw_fm, re.M)
    if m_name:
        name = m_name.group(1).strip().strip("\"'")

    # Extract description block (handling multiline or folded)
    m_desc = re.search(r"^description:\s*(?:>-\s*|>|\s*)([\s\S]*?)(?:^[a-zA-Z0-9_-]+:|\Z)", raw_fm, re.M)
    desc = ""
    if m_desc:
        desc_raw = m_desc.group(1).strip()
        desc = " ".join(line.strip() for line in desc_raw.splitlines() if line.strip()).strip("\"'")
    if not desc:
        desc = f"Skill {name}"

    fm_dict = {"name": name, "description": desc}
    dumped = yaml.dump(fm_dict, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{dumped}\n---\n{body}", True


def sync_claude_skills(
    claude_skills_dir: Optional[str] = None,
    gemini_config_dir: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Sync and validate skills between Claude Code and Antigravity.

    1. Scans claude_skills_dir for SKILL.md files.
    2. Heals invalid or missing YAML frontmatters non-destructively.
    3. Bridges skills into global and workspace Antigravity configurations.
    """
    if claude_skills_dir is None:
        claude_skills_dir = DEFAULT_CLAUDE_SKILLS
    if gemini_config_dir is None:
        gemini_config_dir = DEFAULT_GEMINI_CONFIG
    if workspace_dir is None:
        workspace_dir = os.getcwd()

    if not os.path.exists(claude_skills_dir):
        return {"status": "skipped", "reason": f"Directory {claude_skills_dir} not found"}

    total_scanned = 0
    modified_count = 0
    valid_count = 0
    errors = []

    # 1. Inspect and normalize each skill
    for item in sorted(os.listdir(claude_skills_dir)):
        item_path = os.path.join(claude_skills_dir, item)
        if not os.path.isdir(item_path):
            continue
        skill_file = os.path.join(item_path, "SKILL.md")
        if not os.path.exists(skill_file):
            continue

        total_scanned += 1
        try:
            with open(skill_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            normalized, modified = normalize_skill_frontmatter(content, item)
            if modified:
                modified_count += 1
                if not dry_run:
                    tmp = skill_file + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(normalized)
                    os.replace(tmp, skill_file)

            # Verification of normalized YAML
            parts = normalized.split("---", 2)
            parsed = yaml.safe_load(parts[1])
            if isinstance(parsed, dict) and "name" in parsed and "description" in parsed:
                valid_count += 1
            else:
                errors.append(f"{item}: frontmatter structure invalid")
        except Exception as e:
            errors.append(f"{item}: {str(e)}")

    # 2. Ensure global Antigravity linkage
    global_skills_link = os.path.join(gemini_config_dir, "skills")
    global_skills_json = os.path.join(gemini_config_dir, "skills.json")
    if not dry_run:
        os.makedirs(gemini_config_dir, exist_ok=True)
        # Point global symlink to claude skills
        if not os.path.exists(global_skills_link) and not os.path.islink(global_skills_link):
            try:
                os.symlink(claude_skills_dir, global_skills_link)
            except Exception:
                pass
        # Ensure skills.json registers claude skills explicitly
        skills_cfg = {"entries": [{"path": claude_skills_dir}]}
        try:
            with open(global_skills_json, "w", encoding="utf-8") as f:
                json.dump(skills_cfg, f, indent=2)
        except Exception:
            pass

    # 3. Clean up invalid workspace skills.json and ensure .agents/skills linkage
    if workspace_dir and not dry_run:
        ws_agents = os.path.join(workspace_dir, ".agents")
        is_git_or_agents = os.path.exists(os.path.join(workspace_dir, ".git")) or os.path.exists(ws_agents)
        if is_git_or_agents:
            os.makedirs(ws_agents, exist_ok=True)
            ws_skills_json = os.path.join(ws_agents, "skills.json")
            if os.path.exists(ws_skills_json):
                try:
                    with open(ws_skills_json, "r", encoding="utf-8") as f:
                        ws_cfg = json.load(f)
                    entries = ws_cfg.get("entries", [])
                    norm_ws = os.path.realpath(workspace_dir).rstrip("/")
                    valid_entries = [
                        e for e in entries
                        if not e.get("path", "").startswith("/") or os.path.realpath(e.get("path", "")).startswith(norm_ws + "/")
                    ]
                    if not valid_entries:
                        os.remove(ws_skills_json)
                    elif len(valid_entries) != len(entries):
                        with open(ws_skills_json, "w", encoding="utf-8") as f:
                            json.dump({"entries": valid_entries}, f, indent=2)
                except Exception:
                    pass

            ws_skills_link = os.path.join(ws_agents, "skills")
            if not os.path.exists(ws_skills_link) and not os.path.islink(ws_skills_link):
                try:
                    os.symlink(claude_skills_dir, ws_skills_link)
                except Exception:
                    pass

    return {
        "status": "synced",
        "total_skills": total_scanned,
        "normalized": modified_count,
        "valid": valid_count,
        "errors": errors,
        "source": claude_skills_dir,
    }


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    res = sync_claude_skills(dry_run=is_dry)
    print(json.dumps(res, indent=2))
