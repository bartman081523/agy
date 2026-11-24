#!/usr/bin/env python3
"""Sanitizes skills and prompts for public open-source distribution.

Scans and replaces:
  - Real personal names and family references (plain and URL-encoded)
  - Private LAN IPs, ngrok tunnels, and DuckDNS subdomains
  - Hardcoded absolute user paths (/run/media/julian/..., /home/julian/...)
  - Passwords and private API tokens

Preserves:
  - Fictional names and narrative settings in StoryWriterMind / MindState / MindChat
"""

import os
import re
import shutil
import sys

STORYWRITER_WHITELIST = [
    "StoryWriterMind",
    "StoryWriterMind_v2",
    "MindChat",
    "MindChat_v2",
    "MindState",
    "EmotionalHumanMind",
]

SANITIZATION_RULES = [
    # 1. Passwords (strict safeguard)
    (re.compile(r"penaten86", re.IGNORECASE), "REDACTED_PASSWORD"),
    (re.compile(r"penaten", re.IGNORECASE), "REDACTED"),

    # 2. Specific real personal names & genealogic references (plain + URL encoded)
    (re.compile(r"Grischa(?:\+|%20|\s+)Levin(?:\+|%20|\s+)Lunow", re.I), "Max+Mustermann"),
    (re.compile(r"Grischa(?:\+|%20|\s+)Levin", re.I), "Max"),
    (re.compile(r"\bLunow\b", re.I), "Mustermann"),
    (re.compile(r"\bUrsula(?:\+|%20|\s+)Schulte\b", re.I), "Erika Mustermann"),
    (re.compile(r"\bJulian\s+Schulte-Eckardt\b", re.I), "Dev User"),
    (re.compile(r"\bjuli\.schulte\.86@gmail\.com\b", re.I), "user@example.com"),
    (re.compile(r"\bjuli\.schulte\.eck@gmail\.com\b", re.I), "user@example.com"),
    (re.compile(r"\bjuschueck86@gmail\.com\b", re.I), "user@example.com"),
    (re.compile(r"\bchlorophyll273@gmail\.com\b", re.I), "user@example.com"),
    (re.compile(r"\bbreslau-family-graf-schulte\b", re.I), "genealogy-investigation-sample"),
    (re.compile(r"\bSchulte\s+Breslau\b", re.I), "Mustermann Berlin"),
    (re.compile(r"\bSchulte\+placeOfBirth%3ABreslau\b", re.I), "Mustermann+placeOfBirth%3ABerlin"),
    (re.compile(r"\bSchulte\+birthDate%3A1929\b", re.I), "Mustermann+birthDate%3A1929"),
    (re.compile(r"\bq=Schulte\+birthDate%3A1929\b", re.I), "q=Mustermann+birthDate%3A1929"),

    # 3. Private hosts, tunnels & domains
    (re.compile(r"http://192\.168\.178\.20:10000", re.I), "http://localhost:10000"),
    (re.compile(r"https://gah892lcspw0zemshn34qjnhocv0kavju7l92x8t\.duckdns\.org", re.I), "https://api.example.com"),
    (re.compile(r"https://unvigilant-nonancestrally-onie\.ngrok-free\.dev", re.I), "https://api.example.com"),
    (re.compile(r"\bDuckDNS\b", re.I), "CloudAPI"),
    (re.compile(r"\bngrok\b", re.I), "TunnelAPI"),

    # 4. User and volume paths
    (re.compile(r"/run/media/julian/ML[0-9]/prompts-bartman/prompts/universal/"), "~/.gemini/antigravity-cli/prompts/universal/"),
    (re.compile(r"/run/media/julian/ML[0-9]/prompts-bartman/prompts/"), "~/.gemini/antigravity-cli/prompts/"),
    (re.compile(r"/run/media/julian/ML[0-9]/"), "~/projects/"),
    (re.compile(r"/home/julian/\.claude/skills/"), "~/.gemini/antigravity-cli/skills/"),
    (re.compile(r"/home/julian/"), "~/"),
]


def sanitize_text(text: str, filename: str) -> str:
    is_story = any(w in filename for w in STORYWRITER_WHITELIST)
    result = text
    for pattern, replacement in SANITIZATION_RULES:
        if is_story and "Mustermann" in replacement:
            continue
        result = pattern.sub(replacement, result)
    return result


def export_sanitized_skills(src_dir: str, dst_dir: str) -> int:
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    for root, dirs, files in os.walk(src_dir):
        if ".git" in root or "__pycache__" in root:
            continue
        rel_dir = os.path.relpath(root, src_dir)
        target_dir = os.path.join(dst_dir, rel_dir)
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            if f.endswith(".pyc"):
                continue
            src_file = os.path.join(root, f)
            dst_file = os.path.join(target_dir, f)
            try:
                with open(src_file, "r", encoding="utf-8", errors="ignore") as rfh:
                    content = rfh.read()
                clean = sanitize_text(content, f)
                with open(dst_file, "w", encoding="utf-8") as wfh:
                    wfh.write(clean)
                count += 1
            except Exception as e:
                print(f"Skipping {src_file}: {e}", file=sys.stderr)
    return count


def verify_skills_dir(target_dir: str) -> list[str]:
    """Scans all files in target_dir for potential sensitive leaks."""
    leaks = []
    forbidden_patterns = [
        re.compile(r"penaten86", re.I),
        re.compile(r"Grischa(?:\+|%20|\s+)Levin", re.I),
        re.compile(r"\bLunow\b", re.I),
        re.compile(r"\bUrsula\s+Schulte\b", re.I),
        re.compile(r"\bJulian\s+Schulte", re.I),
        re.compile(r"juli\.schulte", re.I),
        re.compile(r"192\.168\.178\.20"),
        re.compile(r"duckdns\.org", re.I),
        re.compile(r"/run/media/julian/"),
        re.compile(r"/home/julian/"),
    ]
    for root, dirs, files in os.walk(target_dir):
        if ".git" in root or "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".pyc") or f == "sanitize_skills.py":
                continue
            # Whitelist narrative texts
            if any(w in f for w in STORYWRITER_WHITELIST):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as rfh:
                    content = rfh.read()
                for pat in forbidden_patterns:
                    matches = pat.findall(content)
                    if matches:
                        leaks.append(f"{path}: matched {pat.pattern} -> {matches[:3]}")
            except Exception as e:
                leaks.append(f"{path}: read error {e}")
    return leaks


if __name__ == "__main__":
    if "--verify-only" in sys.argv:
        check_dir = sys.argv[sys.argv.index("--verify-only") + 1] if len(sys.argv) > sys.argv.index("--verify-only") + 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
        print(f"Verifying {check_dir} for privacy leaks ...")
        leaks = verify_skills_dir(check_dir)
        if leaks:
            print(f"FAILED: Found {len(leaks)} potential leaks:")
            for l in leaks:
                print(f"  {l}")
            sys.exit(1)
        print("PASS: 0 leaks found.")
        sys.exit(0)

    src = os.path.expanduser("~/.claude/skills")
    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    print(f"Sanitizing skills from {src} to {dst} ...")
    n = export_sanitized_skills(src, dst)
    print(f"Successfully sanitized and exported {n} skill files.")

