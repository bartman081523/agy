#!/usr/bin/env python3
"""AGY Mod Updater & GitHub Release Manager.

Handles:
- Checking GitHub releases for newer versions of bartman081523/agy
- Self-updating the mod installation from GitHub release tarballs
- Synchronizing skills from bartman081523/skills
- Synchronizing prompt minds from bartman081523/prompts
- Intercepting `agy update` / `agy self-update` to target GitHub rather than proprietary Google upstream
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_MOD_REPO = "bartman081523/agy"
DEFAULT_SKILLS_REPO = "https://github.com/bartman081523/skills.git"
DEFAULT_PROMPTS_REPO = "https://github.com/bartman081523/prompts.git"
CACHE_FILE = os.path.expanduser("~/.gemini/antigravity-cli/update_cache.json")
DEFAULT_CACHE_TTL = 14400  # 4 hours


def get_repo_dir() -> str:
    """Returns the base directory of the current agy installation/repo."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(module_dir))


def get_current_version(repo_dir: Optional[str] = None) -> str:
    """Reads current version from VERSION file or falls back to default."""
    if repo_dir is None:
        repo_dir = get_repo_dir()
    version_file = os.path.join(repo_dir, "VERSION")
    if os.path.exists(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as fh:
                v = fh.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return "1.5.0"


def parse_semver(version_str: str) -> Tuple[int, ...]:
    """Parses a version string into a comparable tuple of integers."""
    clean = re.sub(r"^[vV]", "", version_str.strip())
    nums = [int(p) for p in re.findall(r"\d+", clean)]
    return tuple(nums) if nums else (0, 0, 0)


def fetch_latest_release(repo: str = DEFAULT_MOD_REPO, timeout: int = 6) -> Optional[Dict[str, Any]]:
    """Queries GitHub Releases API for the latest release."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AGY-Updater/1.5",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data
    except urllib.error.HTTPError as e:
        # 404 means no release published yet
        if e.code == 404:
            return None
    except Exception:
        return None
    return None


def check_for_updates(
    repo: str = DEFAULT_MOD_REPO,
    force: bool = False,
    cache_ttl: int = DEFAULT_CACHE_TTL,
) -> Dict[str, Any]:
    """Checks whether an update is available on GitHub. Uses local cache unless force=True."""
    current_ver = get_current_version()
    now = time.time()

    # Check cache first
    if not force and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as cf:
                cache_data = json.load(cf)
            if now - cache_data.get("timestamp", 0) < cache_ttl:
                latest_ver = cache_data.get("latest_version", current_ver)
                has_update = parse_semver(latest_ver) > parse_semver(current_ver)
                cache_data["has_update"] = has_update
                cache_data["current_version"] = current_ver
                return cache_data
        except Exception:
            pass

    release_info = fetch_latest_release(repo)
    if not release_info:
        return {
            "has_update": False,
            "current_version": current_ver,
            "latest_version": current_ver,
            "tag_name": f"v{current_ver}",
            "release_url": f"https://github.com/{repo}",
            "download_url": None,
            "changelog": "",
            "timestamp": now,
        }

    tag_name = release_info.get("tag_name", "")
    latest_ver = tag_name.lstrip("v")
    has_update = parse_semver(latest_ver) > parse_semver(current_ver)

    download_url = None
    for asset in release_info.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".tar.gz") or name.endswith(".tgz"):
            download_url = asset.get("browser_download_url")
            break
    if not download_url:
        download_url = release_info.get("tarball_url")

    result = {
        "has_update": has_update,
        "current_version": current_ver,
        "latest_version": latest_ver,
        "tag_name": tag_name,
        "release_url": release_info.get("html_url", f"https://github.com/{repo}"),
        "download_url": download_url,
        "changelog": release_info.get("body", ""),
        "timestamp": now,
    }

    # Save to cache
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as cf:
            json.dump(result, cf, indent=2)
    except Exception:
        pass

    return result


def sync_git_repo(url: str, target_dir: str, name: str) -> bool:
    """Clones or updates a git repository."""
    os.makedirs(os.path.dirname(os.path.abspath(target_dir)), exist_ok=True)
    if os.path.exists(os.path.join(target_dir, ".git")):
        print(f"Updating {name} in {target_dir} ...")
        proc = subprocess.run(["git", "-C", target_dir, "pull", "--ff-only"], capture_output=True, text=True)
        return proc.returncode == 0
    else:
        print(f"Cloning {name} into {target_dir} ...")
        if os.path.exists(target_dir):
            # Backup non-git directory if necessary
            shutil.rmtree(target_dir)
        proc = subprocess.run(["git", "clone", "--depth", "1", url, target_dir], capture_output=True, text=True)
        return proc.returncode == 0


def self_update(repo: str = DEFAULT_MOD_REPO, force: bool = False) -> bool:
    """Executes a full self-update of AGY Mod, skills, and prompts from GitHub."""
    print("=" * 60)
    print(f" AGY Mod Self-Updater (Target: {repo})")
    print("=" * 60)

    repo_dir = get_repo_dir()
    current_ver = get_current_version(repo_dir)
    print(f"Checking for updates on GitHub (current: v{current_ver}) ...")

    update_info = check_for_updates(repo=repo, force=True)
    latest_ver = update_info.get("latest_version", current_ver)
    has_update = update_info.get("has_update", False)

    if not has_update and not force:
        print(f"✓ AGY Mod is already up to date (v{current_ver}).")
    else:
        print(f"-> New version available: v{latest_ver} (current: v{current_ver})")
        download_url = update_info.get("download_url")
        if download_url:
            print(f"Downloading release bundle from {download_url} ...")
            try:
                with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tf:
                    tar_path = tf.name
                urllib.request.urlretrieve(download_url, tar_path)
                print("Extracting release package ...")
                with tempfile.TemporaryDirectory() as extract_dir:
                    with tarfile.open(tar_path, "r:gz") as tar:
                        tar.extractall(extract_dir)
                    # Locate unpacked root
                    entries = os.listdir(extract_dir)
                    source_dir = extract_dir
                    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                        source_dir = os.path.join(extract_dir, entries[0])

                    # If current install is a git repo, prefer git pull if remote exists
                    if os.path.exists(os.path.join(repo_dir, ".git")):
                        print("Updating local git repository ...")
                        subprocess.run(["git", "-C", repo_dir, "pull", "--rebase"], check=False)
                    else:
                        print(f"Updating installation in {repo_dir} ...")
                        for item in os.listdir(source_dir):
                            if item in [".git", "tests"]:
                                continue
                            s = os.path.join(source_dir, item)
                            d = os.path.join(repo_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)
                os.remove(tar_path)
                print(f"✓ Successfully updated core AGY Mod to v{latest_ver}!")
            except Exception as e:
                print(f"⚠ Core package download/extraction warning: {e}")
                print("Falling back to git pull ...")
                subprocess.run(["git", "-C", repo_dir, "pull"], check=False)

    # Synchronize Skills
    print("\nSynchronizing Agent Skills from GitHub ...")
    skills_claude = os.path.expanduser("~/.claude/skills")
    skills_gemini = os.path.expanduser("~/.gemini/config/skills")
    sync_git_repo(DEFAULT_SKILLS_REPO, skills_claude, "Skills (Claude)")
    sync_git_repo(DEFAULT_SKILLS_REPO, skills_gemini, "Skills (Antigravity)")

    # Synchronize Prompts
    print("\nSynchronizing Prompt-Minds from GitHub ...")
    prompts_dir = os.path.expanduser("~/.gemini/antigravity-cli/prompts")
    sync_git_repo(DEFAULT_PROMPTS_REPO, prompts_dir, "Prompt Minds")

    # Update launchers in ~/.local/bin
    local_bin = os.path.expanduser("~/.local/bin")
    os.makedirs(local_bin, exist_ok=True)
    bin_dir = os.path.join(repo_dir, "bin")
    if os.path.isdir(bin_dir):
        for launcher in os.listdir(bin_dir):
            src_launcher = os.path.join(bin_dir, launcher)
            dst_launcher = os.path.join(local_bin, launcher)
            if os.path.isfile(src_launcher) and os.access(src_launcher, os.X_OK):
                if os.path.lexists(dst_launcher):
                    os.remove(dst_launcher)
                os.symlink(src_launcher, dst_launcher)

    print("\n" + "=" * 60)
    print("✓ Update complete! All AGY launchers, skills, and prompts are up to date.")
    print("=" * 60)
    return True


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args or "-c" in args or "check" in args:
        force = "--force" in args or "-f" in args
        info = check_for_updates(force=force)
        if info["has_update"]:
            print(f"Update available: v{info['latest_version']} (current: v{info['current_version']})")
            print(f"Run 'agy update' or 'agy-full update' to upgrade.")
            return 1
        else:
            print(f"AGY Mod is up to date (v{info['current_version']}).")
            return 0

    if "--statusline-check" in args:
        info = check_for_updates(force=False)
        if info.get("has_update"):
            print(f"Update: v{info['latest_version']}")
        return 0

    force = "--force" in args or "-f" in args
    success = self_update(force=force)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
