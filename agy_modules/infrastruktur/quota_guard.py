"""AGY Quota Guard

Monitors AGY sessions for Gemini quota exhaustion (RESOURCE_EXHAUSTED / code 429).
Parses the exact reset duration from server messages (e.g. 'Resets in 31m39s', '10m15s',
'1h20m', 'Wieder aufgefüllt in ...'), displays the remaining time in the terminal,
waits until the quota is replenished, and automatically resumes the session seamlessly.

Advanced Capabilities:
  - Last-Error Debounce: when multiple quota messages arrive in rapid succession
    (retries, burst streaming, replayed history), the guard handles ONLY the latest/newest
    error message and ignores earlier ones.
  - Multi-Account Pool (up to 7 accounts or dynamic N): manages account pools, measures real
    remaining quota per account, and when an account is exhausted, automatically switches to
    the account with the MOST remaining quota.
  - Smart Cooldown Fallback: if all accounts are exhausted, selects the account that resets
    soonest and waits only for that shortest interval.
  - TUI Live Status Bar: in interactive sessions, displays a bottom status bar with the current
    account, real remaining quota %, and current conversation context fill (used vs window size).
  - Timestamp anchoring: countdown is anchored to the message timestamp.
  - ESC-interruptible wait: pressing ESC aborts waiting and hands control to the user.
"""

import sys
import os
import re
import json
import time
import signal
import threading
import select
import shutil
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any, Callable


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def parse_reset_duration(text: str) -> Optional[float]:
    """Parse remaining quota reset duration in seconds from an error message or log line.

    Supports formats like:
      - 'Resets in 31m39s.'
      - 'Resets in 10m15s.'
      - 'Resets in 1h20m5s.'
      - 'Resets in 2h.'
      - 'Resets in 45s.'
      - 'Resets in 2 hours, 15 minutes, 30 seconds'
      - 'Quote wieder aufgefüllt in 15 Min 30 Sek'
      - 'resetTime: "2026-10-22T03:32:00Z"'
    """
    if not text:
        return None

    # Check for ISO timestamp format (e.g. resetTime: "2026-10-22T03:32:00Z")
    iso_match = re.search(r'resetTime[:=]\s*"?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"?', text, re.I)
    if iso_match:
        try:
            target_str = iso_match.group(1).replace('Z', '+00:00')
            target_dt = datetime.fromisoformat(target_str)
            now_dt = datetime.now(timezone.utc) if target_dt.tzinfo else datetime.utcnow()
            diff = (target_dt - now_dt).total_seconds()
            return max(0.0, diff)
        except Exception:
            pass

    # Pattern A: Find reset prefix (English or German)
    prefix_match = re.search(r'(?:(?:quota|quote|kontingent)[^.\n]*?)?(?:resets?|wieder\s+aufgefüllt|wieder\s+da|verfügbar)\s+in\s+', text, re.I)
    if prefix_match:
        rem = text[prefix_match.end():]
        # Only take up to the next sentence or line break
        segment = re.split(r'[\.\n\r]', rem)[0]
        h = re.search(r'(\d+)\s*(?:std|stunden?|hours?|h(?![a-zA-Z]))', segment, re.I)
        m = re.search(r'(\d+)\s*(?:min|minuten?|minutes?|m(?![a-zA-Z]))', segment, re.I)
        s = re.search(r'(\d+)\s*(?:sek|sekunden?|seconds?|secs?|s(?![a-zA-Z]))', segment, re.I)
        if h or m or s:
            h_val = int(h.group(1)) if h else 0
            m_val = int(m.group(1)) if m else 0
            s_val = int(s.group(1)) if s else 0
            total = h_val * 3600 + m_val * 60 + s_val
            if total > 0:
                return float(total)

    # Pattern B: Compact format fallback like 'Resets in 31m39s'
    m_compact = re.search(r'Resets?\s+in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', text, re.I)
    if m_compact and any(m_compact.groups()):
        h_val = int(m_compact.group(1)) if m_compact.group(1) else 0
        m_val = int(m_compact.group(2)) if m_compact.group(2) else 0
        s_val = int(m_compact.group(3)) if m_compact.group(3) else 0
        total = h_val * 3600 + m_val * 60 + s_val
        if total > 0:
            return float(total)

    return None


def parse_log_line_timestamp(line: str) -> Optional[float]:
    """Extract epoch seconds from a glog-style log line prefix (e.g. 'I1023 04:55:59.240652')."""
    m = re.match(r'([IWEF])(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?', line)
    if not m:
        return None
    try:
        month = int(m.group(2)[0:2])
        day = int(m.group(2)[2:4])
        hour = int(m.group(3))
        minute = int(m.group(4))
        second = int(m.group(5))

        now_local = datetime.now()
        for y in (now_local.year, now_local.year - 1):
            try:
                dt = datetime(y, month, day, hour, minute, second)
            except ValueError:
                continue
            if dt <= now_local:
                return dt.timestamp()
        dt = datetime(now_local.year, month, day, hour, minute, second)
        return dt.timestamp()
    except Exception:
        return None


def format_remaining_time(seconds: float) -> str:
    """Format seconds into a human-readable duration string (e.g. '31m 39s' or '1h 05m 12s')."""
    total_sec = max(0, int(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"


# ---------------------------------------------------------------------------
# Log discovery & Context Fill
# ---------------------------------------------------------------------------

def find_latest_active_log_file() -> Optional[str]:
    """Find the most recent CLI log file in ~/.gemini/antigravity-cli/."""
    home = os.path.expanduser("~")
    base_dir = os.path.join(home, ".gemini", "antigravity-cli")
    symlink_path = os.path.join(base_dir, "cli.log")
    if os.path.exists(symlink_path):
        return os.path.realpath(symlink_path)

    log_dir = os.path.join(base_dir, "log")
    if os.path.isdir(log_dir):
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith("cli-") and f.endswith(".log")]
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            return files[0]
    return None


def get_latest_context_fill(session_id: Optional[str] = None) -> Tuple[int, int, float]:
    """Returns (used_tokens, window_size, pct) of conversation context fill."""
    base_dir = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain")
    window_size = 1_048_576  # Default 1M tokens window for Gemini Flash/Pro

    candidate_transcripts: List[str] = []
    if session_id:
        candidate_transcripts.append(os.path.join(base_dir, session_id, ".system_generated", "logs", "transcript.jsonl"))

    if os.path.isdir(base_dir):
        others = []
        for d in os.listdir(base_dir):
            tp = os.path.join(base_dir, d, ".system_generated", "logs", "transcript.jsonl")
            if os.path.exists(tp):
                others.append(tp)
        others.sort(key=os.path.getmtime, reverse=True)
        for tp in others[:3]:
            if tp not in candidate_transcripts:
                candidate_transcripts.append(tp)

    for tp in candidate_transcripts:
        if not os.path.exists(tp):
            continue
        try:
            with open(tp, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "PLANNER_RESPONSE" and obj.get("input_tokens") is not None:
                            inp = obj.get("input_tokens") or 0
                            out = obj.get("output_tokens") or 0
                            cache = obj.get("cache_read_tokens") or 0
                            total = inp + cache + out
                            pct = min(100.0, (total / window_size) * 100.0)
                            return (total, window_size, pct)
                    except Exception:
                        continue
        except Exception:
            continue

    return (0, window_size, 0.0)


# ---------------------------------------------------------------------------
# Timestamp anchoring
# ---------------------------------------------------------------------------

def get_last_message_timestamp(session_id: Optional[str] = None) -> Optional[float]:
    """Anchor timestamp for countdown: timestamp of the last message carrying quota notice."""
    base_dir = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli")
    brain_dir = os.path.join(base_dir, "brain")

    transcript_paths: List[str] = []
    if session_id:
        transcript_paths.append(os.path.join(
            brain_dir, session_id, ".system_generated", "logs", "transcript.jsonl"
        ))
    try:
        if os.path.isdir(brain_dir):
            others = []
            for d in os.listdir(brain_dir):
                tp = os.path.join(brain_dir, d, ".system_generated", "logs", "transcript.jsonl")
                if os.path.exists(tp):
                    others.append(tp)
            others.sort(key=os.path.getmtime, reverse=True)
            for tp in others[:3]:
                if tp not in transcript_paths:
                    transcript_paths.append(tp)
    except Exception:
        pass

    for tp in transcript_paths:
        if not os.path.exists(tp):
            continue
        ts = _transcript_quota_timestamp(tp)
        if ts:
            return ts

    for candidate in transcript_paths[:1]:
        try:
            return os.path.getmtime(candidate)
        except Exception:
            continue

    log_path = find_latest_active_log_file()
    if log_path and os.path.exists(log_path):
        try:
            return os.path.getmtime(log_path)
        except Exception:
            pass

    return None


def _transcript_quota_timestamp(transcript_path: str) -> Optional[float]:
    """created_at of the last event in a transcript.jsonl that mentions a quota reset."""
    marker_re = re.compile(
        r"(RESOURCE_EXHAUSTED|resets?\s+in|wieder\s+aufgefüllt|individual quota|429)", re.I
    )
    last_quota_ts: Optional[str] = None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if not marker_re.search(line[:2000]):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not marker_re.search(str(obj.get("content", "")) or ""):
                    continue
                if obj.get("created_at"):
                    last_quota_ts = obj["created_at"]
    except Exception:
        return None
    return _parse_iso_z(last_quota_ts) if last_quota_ts else None


def _parse_iso_z(value: str) -> Optional[float]:
    """Parse an ISO-8601 UTC timestamp like '2026-10-23T13:08:43Z'."""
    if not value:
        return None
    try:
        val = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def compute_remaining_seconds(
    duration_seconds: float,
    anchor_ts: Optional[float],
    now: Optional[float] = None,
) -> float:
    """Compute still-to-wait time, anchored at the last message timestamp."""
    now = time.time() if now is None else now
    if not anchor_ts:
        return max(0.0, duration_seconds)
    elapsed = max(0.0, now - anchor_ts)
    return max(0.0, duration_seconds - elapsed)


class QuotaGuardState:
    """Persisted anchor across restarts of the supervisor."""

    MAX_AGE_SECONDS = 24 * 3600

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".gemini", "antigravity-cli", "quota_anchor.json"
        )

    def read(self) -> Optional[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict) and state.get("target_time"):
                persisted_at = float(state.get("persisted_at") or 0)
                if persisted_at and time.time() - persisted_at > self.MAX_AGE_SECONDS:
                    return None
                return state
        except Exception:
            pass
        return None

    def write(self, state: dict) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Account Pool & Quota Measurement (Up to 7 Accounts)
# ---------------------------------------------------------------------------

DEFAULT_ACCOUNTS_FILE = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "accounts.json")
DEFAULT_QUOTAS_STATE_FILE = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "account_quotas.json")


class AccountProfile:
    """Represents a single account slot with its credentials and measured quota health."""

    def __init__(
        self,
        index: int,
        name: str = "",
        cli_prefix: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        exhausted_until: float = 0.0,
        last_reset_duration: float = 0.0,
        recent_tokens: int = 0,
        last_used_ts: float = 0.0,
    ):
        self.index = index
        self.name = name or f"Account {index + 1}"
        self.cli_prefix = cli_prefix or []
        self.env = env or {}
        self.exhausted_until = exhausted_until
        self.last_reset_duration = last_reset_duration
        self.recent_tokens = recent_tokens
        self.last_used_ts = last_used_ts

    def is_available(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.exhausted_until

    def remaining_cooldown(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self.exhausted_until - now)

    def remaining_quota_pct(self, now: Optional[float] = None, token_capacity: int = 1_000_000) -> float:
        now = time.time() if now is None else now
        if not self.is_available(now):
            return 0.0
        if self.recent_tokens > 0:
            used_ratio = min(1.0, self.recent_tokens / token_capacity)
            return max(5.0, round((1.0 - used_ratio) * 100.0, 1))
        return 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "cli_prefix": self.cli_prefix,
            "env": self.env,
            "exhausted_until": self.exhausted_until,
            "last_reset_duration": self.last_reset_duration,
            "recent_tokens": self.recent_tokens,
            "last_used_ts": self.last_used_ts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountProfile":
        return cls(
            index=int(data.get("index", 0)),
            name=str(data.get("name", "")),
            cli_prefix=list(data.get("cli_prefix", [])),
            env=dict(data.get("env", {})),
            exhausted_until=float(data.get("exhausted_until", 0.0)),
            last_reset_duration=float(data.get("last_reset_duration", 0.0)),
            recent_tokens=int(data.get("recent_tokens", 0)),
            last_used_ts=float(data.get("last_used_ts", 0.0)),
        )


class MultiAccountManager:
    """Manages up to 7 (or more) accounts and tracks their real remaining quotas."""

    def __init__(
        self,
        config_path: str = DEFAULT_ACCOUNTS_FILE,
        state_path: str = DEFAULT_QUOTAS_STATE_FILE,
        explicit_prefixes: Optional[List[str]] = None,
    ):
        self.config_path = config_path
        self.state_path = state_path
        self.accounts: List[AccountProfile] = []
        self._load_or_init(explicit_prefixes)
        self._load_quota_state()

    def _load_or_init(self, explicit_prefixes: Optional[List[str]] = None) -> None:
        """Load from explicit prefixes, accounts.json, or initialize default slots (up to 7)."""
        if explicit_prefixes:
            for i, p in enumerate(explicit_prefixes):
                pref_list = resolve_cli_prefix(p)
                name = f"Account {i + 1}"
                if len(pref_list) > 0 and pref_list[0].startswith("--"):
                    name = pref_list[0].lstrip("-")
                self.accounts.append(AccountProfile(index=i, name=name, cli_prefix=pref_list))
            return

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                acc_list = data if isinstance(data, list) else data.get("accounts", [])
                for i, item in enumerate(acc_list):
                    if isinstance(item, str):
                        self.accounts.append(AccountProfile(index=i, name=f"Account {i + 1}", cli_prefix=resolve_cli_prefix(item)))
                    elif isinstance(item, dict):
                        pref = item.get("cli_prefix") or []
                        if isinstance(pref, str):
                            pref = resolve_cli_prefix(pref)
                        self.accounts.append(AccountProfile(
                            index=i,
                            name=item.get("name", f"Account {i + 1}"),
                            cli_prefix=pref,
                            env=item.get("env", {})
                        ))
                if self.accounts:
                    return
            except Exception:
                pass

        # Default fallback: single default account unless accounts.json defines multiple
        self.accounts.append(AccountProfile(index=0, name="Default", cli_prefix=[]))

    def _load_quota_state(self) -> None:
        """Load persisted quota readiness and token metrics."""
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            now = time.time()
            for key, val in saved.items():
                try:
                    idx = int(key)
                    if 0 <= idx < len(self.accounts):
                        ex_until = float(val.get("exhausted_until", 0.0))
                        # Only keep if not ancient (>24h)
                        if ex_until > now - 86400:
                            self.accounts[idx].exhausted_until = ex_until
                            self.accounts[idx].last_reset_duration = float(val.get("last_reset_duration", 0.0))
                            self.accounts[idx].recent_tokens = int(val.get("recent_tokens", 0))
                            self.accounts[idx].last_used_ts = float(val.get("last_used_ts", 0.0))
                except Exception:
                    continue
        except Exception:
            pass

    def persist_quota_state(self) -> None:
        """Save account quota metrics to disk."""
        try:
            data = {str(acc.index): acc.to_dict() for acc in self.accounts}
            tmp = self.state_path + ".tmp"
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    def mark_exhausted(self, index: int, duration_seconds: float, source_ts: Optional[float] = None) -> None:
        """Mark account exhausted for duration_seconds."""
        now = time.time()
        anchor = source_ts if source_ts else now
        if 0 <= index < len(self.accounts):
            acc = self.accounts[index]
            acc.exhausted_until = anchor + duration_seconds
            acc.last_reset_duration = duration_seconds
            acc.recent_tokens = 1_000_000  # Depleted
            acc.last_used_ts = now
            self.persist_quota_state()

    def record_usage(self, index: int, tokens: int) -> None:
        """Record tokens consumed by account."""
        if 0 <= index < len(self.accounts):
            acc = self.accounts[index]
            acc.recent_tokens += tokens
            acc.last_used_ts = time.time()
            self.persist_quota_state()

    def select_best_account(self, exclude_index: Optional[int] = None) -> Tuple[int, float, str]:
        """Select the account with the MOST remaining quota.

        Returns (best_index, wait_seconds_needed, reason):
          - If one or more accounts are available: returns best account with 0.0s wait.
          - If ALL accounts are exhausted: returns the account whose cooldown ends SOONEST
            with the remaining wait seconds!
        """
        now = time.time()
        candidates = [
            acc for acc in self.accounts
            if exclude_index is None or acc.index != exclude_index
        ]
        if not candidates:
            # Fallback to current if only 1 account exists
            acc = self.accounts[0] if self.accounts else AccountProfile(0)
            return (acc.index, acc.remaining_cooldown(now), "single_account")

        # 1. Accounts available right now
        available = [acc for acc in candidates if acc.is_available(now)]
        if available:
            # Sort by highest remaining quota %; tie-breaker: least recently used
            available.sort(key=lambda a: (a.remaining_quota_pct(now), -a.last_used_ts), reverse=True)
            best = available[0]
            pct = best.remaining_quota_pct(now)
            return (best.index, 0.0, f"available_{pct:.0f}%")

        # 2. All exhausted: select the one that resets earliest
        candidates.sort(key=lambda a: a.remaining_cooldown(now))
        best = candidates[0]
        wait_needed = best.remaining_cooldown(now)
        return (best.index, wait_needed, f"earliest_reset_{wait_needed:.0f}s")


# ---------------------------------------------------------------------------
# Account configuration helpers
# ---------------------------------------------------------------------------

def parse_accounts(spec: Optional[str]) -> List[str]:
    """Parse the --quota-accounts spec into a list of CLI prefixes."""
    if not spec or not str(spec).strip():
        return []

    spec = str(spec).strip()

    if os.path.exists(spec):
        try:
            with open(spec, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return []

        try:
            data = json.loads(content)
        except Exception:
            data = None

        if isinstance(data, list):
            accounts: List[str] = []
            for entry in data:
                if isinstance(entry, list):
                    accounts.append(" ".join(str(x) for x in entry).strip())
                elif isinstance(entry, str):
                    accounts.append(entry.strip())
                elif isinstance(entry, dict) and "cli_prefix" in entry:
                    pref = entry["cli_prefix"]
                    if isinstance(pref, list):
                        accounts.append(" ".join(str(x) for x in pref).strip())
                    else:
                        accounts.append(str(pref).strip())
            return [a for a in accounts if a]

        return [line.strip() for line in content.splitlines() if line.strip()]

    if "," in spec or " " in spec:
        return [a.strip() for a in re.split(r"[,\s]+", spec) if a.strip()]

    return [spec]


def resolve_cli_prefix(account_prefix: str) -> List[str]:
    """Split a stored account prefix into an argv fragment."""
    import shlex
    try:
        return shlex.split(account_prefix)
    except ValueError:
        return account_prefix.split()


# ---------------------------------------------------------------------------
# QuotaGuard Supervisor Core
# ---------------------------------------------------------------------------

class QuotaGuard:
    """Supervises an AGY process and automatically waits & resumes on quota limits."""

    def __init__(
        self,
        buffer_seconds: float = 3.0,
        continuation_prompt: str = "continue",
        anchor_to_last_message: bool = True,
        session_id: Optional[str] = None,
        state: Optional[QuotaGuardState] = None,
        account_manager: Optional[MultiAccountManager] = None,
    ):
        self.buffer_seconds = buffer_seconds
        self.continuation_prompt = continuation_prompt
        self.anchor_to_last_message = anchor_to_last_message
        self.session_id = session_id
        self.last_log_offset = 0
        self.log_file = None
        self._stop_event = threading.Event()
        self.state = state or QuotaGuardState()
        if account_manager is None:
            state_dir = os.path.dirname(os.path.abspath(self.state.path))
            self.account_manager = MultiAccountManager(
                state_path=os.path.join(state_dir, "account_quotas.json")
            )
        else:
            self.account_manager = account_manager

        # Debounce state for consecutive quota messages
        self.last_quota_trigger_ts = 0.0
        self.last_quota_duration = 0.0
        self.debounce_window_seconds = 3.0

    def _resolved_duration(self, duration_seconds: float, source_timestamp: Optional[float] = None) -> Tuple[float, Optional[float]]:
        now = time.time()
        if source_timestamp:
            return compute_remaining_seconds(duration_seconds, source_timestamp, now=now), source_timestamp

        if self.anchor_to_last_message:
            anchor = get_last_message_timestamp(self.session_id)
            if anchor:
                remaining = compute_remaining_seconds(duration_seconds, anchor, now=now)
                return (max(0.0, remaining), anchor)

        persisted = self.state.read()
        if persisted and persisted.get("target_time"):
            remaining = float(persisted["target_time"]) - now
            if remaining > 0:
                return max(0.0, remaining), None

        return max(0.0, duration_seconds), None

    def _persist_anchor(self, remaining: float) -> None:
        self.state.write({
            "target_time": time.time() + remaining + self.buffer_seconds,
            "persisted_at": time.time(),
        })

    def clear_anchor(self) -> None:
        self.state.clear()

    def _pending_remaining(self, duration_seconds: float, source_timestamp: Optional[float] = None) -> float:
        if source_timestamp:
            return compute_remaining_seconds(duration_seconds, source_timestamp)
        return max(0.0, duration_seconds)

    def scan_for_quota_error(
        self,
        text_or_log_path: Optional[str] = None,
        respect_offset: bool = True,
    ) -> Optional[Tuple[float, Optional[float]]]:
        """Scan text or log lines for quota exhaustion errors.

        DEBOUNCE / LAST-ERROR WINS:
        Always inspects lines in reverse order so only the LAST (newest) quota message
        is processed. Earlier messages in the same burst/stream are ignored.
        """
        # 1. Direct text scan
        if text_or_log_path and not os.path.exists(text_or_log_path):
            if "RESOURCE_EXHAUSTED" in text_or_log_path or "quota" in text_or_log_path.lower():
                lines = text_or_log_path.splitlines()
                # Reverse search: last error line wins!
                for line in reversed(lines):
                    if "RESOURCE_EXHAUSTED" in line or ("quota" in line.lower() and ("limit" in line.lower() or "reach" in line.lower() or "reset" in line.lower())):
                        dur = parse_reset_duration(line)
                        if dur is not None:
                            return (dur, parse_log_line_timestamp(line))
                # Fallback to full text if no line matched
                dur = parse_reset_duration(text_or_log_path)
                if dur is not None:
                    return (dur, None)

        # 2. Log file scan
        log_path = text_or_log_path if (text_or_log_path and os.path.exists(text_or_log_path)) else find_latest_active_log_file()
        if not log_path or not os.path.exists(log_path):
            return None

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                start = self.last_log_offset if respect_offset else 0
                f.seek(min(start, os.path.getsize(log_path)))
                new_data = f.read()
                self.last_log_offset = f.tell()

            if not new_data:
                return None

            lines = new_data.splitlines()
            # Reverse search: newest line wins
            for line in reversed(lines):
                if "RESOURCE_EXHAUSTED" in line or ("quota" in line.lower() and ("limit" in line.lower() or "reach" in line.lower() or "reset" in line.lower())):
                    dur = parse_reset_duration(line)
                    if dur is not None:
                        return (dur, parse_log_line_timestamp(line))
        except Exception:
            pass

        return None

    def wait_until_replenished(
        self,
        duration_seconds: float,
        status_callback: Optional[Callable[[str], None]] = None,
        anchor_ts: Optional[float] = None,
        abortable: bool = True,
        passthrough: Optional[Callable[[bytes], None]] = None,
    ) -> bool:
        """Wait until quota is replenished, displaying continuous status updates."""
        orig_tc = None
        if anchor_ts is None:
            _, anchor_ts = self._resolved_duration(duration_seconds)
            remaining = compute_remaining_seconds(duration_seconds, anchor_ts)
        else:
            remaining = max(0.0, duration_seconds)

        if self._stop_event.is_set():
            return False

        target_time = time.time() + remaining + self.buffer_seconds
        if remaining <= 0:
            self.state.clear()
            return True

        self._persist_anchor(max(0.0, remaining))

        reset_time_str = datetime.fromtimestamp(target_time).strftime("%H:%M:%S")
        anchored_note = ""
        if anchor_ts and self.anchor_to_last_message:
            anchored_note = f" (Anker: letzte Nachricht {datetime.fromtimestamp(anchor_ts).strftime('%H:%M:%S')})"

        msg = (
            f"\n\r[AGY Quota Guard] Quota-Limit erreicht (Wartezeit: {format_remaining_time(remaining)}).{anchored_note}\n\r"
            f"[AGY Quota Guard] Pausiere automatisch bis {reset_time_str}. ESC = Warte abbrechen & Command übernehmen...\n\r"
        )
        if status_callback:
            status_callback(msg)
        else:
            sys.stdout.write(msg)
            sys.stdout.flush()

        interrupted = {"signalled": False}

        def _sig_handler(signum, frame):
            interrupted["signalled"] = True

        _old_int = signal.getsignal(signal.SIGINT)
        _old_term = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGINT, _sig_handler)
            signal.signal(signal.SIGTERM, _sig_handler)
        except (ValueError, OSError):
            pass

        esc_pressed = {"event": None}
        watcher_thread: Optional[threading.Thread] = None

        if abortable and not status_callback and hasattr(sys.stdin, "fileno"):
            try:
                stdin_fd = sys.stdin.fileno()
                if os.isatty(stdin_fd):
                    # Switch off ICANON so individual bytes (e.g. ESC 0x1B) are read immediately
                    orig_tc = None
                    try:
                        import termios
                        orig_tc = termios.tcgetattr(stdin_fd)
                        raw_tc = termios.tcgetattr(stdin_fd)
                        raw_tc[3] = raw_tc[3] & ~termios.ICANON
                        termios.tcsetattr(stdin_fd, termios.TCSANOW, raw_tc)
                    except Exception:
                        pass

                    def _stdin_watcher():
                        while not self._stop_event.is_set() and not interrupted["signalled"]:
                            try:
                                r, _, _ = select.select([stdin_fd], [], [], 0.2)
                                if stdin_fd in r:
                                    ch = os.read(stdin_fd, 1)
                                    if ch == b"\x1b":
                                        esc_pressed["event"] = True
                                        self._stop_event.set()
                                        break
                                    elif passthrough and ch:
                                        passthrough(ch)
                            except Exception:
                                break

                    watcher_thread = threading.Thread(target=_stdin_watcher, daemon=True)
                    watcher_thread.start()
            except Exception:
                pass

        try:
            last_status_update = 0.0
            while not self._stop_event.is_set() and not interrupted["signalled"]:
                now = time.time()
                remaining = max(0.0, target_time - now)
                if remaining <= 0:
                    break

                if now - last_status_update >= 1.0:
                    status_line = f"[AGY Quota Guard] Verbleibende Wartezeit: {format_remaining_time(remaining)} ... (ESC = abbrechen) "
                    if status_callback:
                        status_callback(status_line)
                    else:
                        sys.stdout.write(status_line)
                        sys.stdout.flush()
                    last_status_update = now

                time.sleep(0.1)

            if interrupted["signalled"]:
                sig_msg = "\n\r[AGY Quota Guard] Wartezeit durch Signal abgebrochen.\n\r"
                if status_callback:
                    status_callback(sig_msg)
                else:
                    sys.stdout.write(sig_msg)
                    sys.stdout.flush()
                return False

            if esc_pressed["event"]:
                esc_msg = "\n\r[AGY Quota Guard] Wartezeit abgebrochen — ESC/Signal als Command behandelt, keine Auto-Fortsetzung.\n\r"
                if status_callback:
                    status_callback(esc_msg)
                else:
                    sys.stdout.write(esc_msg)
                    sys.stdout.flush()
                return False

            if self._stop_event.is_set():
                return False

            if remaining <= 0:
                self.state.clear()
                done_msg = "\n\r[AGY Quota Guard] Quota wieder aufgefüllt! Setze Programmierung automatisch fort...\n\r"
                if status_callback:
                    status_callback(done_msg)
                else:
                    sys.stdout.write(done_msg)
                    sys.stdout.flush()
                return True

            return False
        finally:
            self._stop_event.clear()
            if orig_tc:
                try:
                    import termios
                    termios.tcsetattr(stdin_fd, termios.TCSANOW, orig_tc)
                except Exception:
                    pass
            try:
                signal.signal(signal.SIGINT, _old_int)
                signal.signal(signal.SIGTERM, _old_term)
            except (ValueError, OSError):
                pass
            if watcher_thread and watcher_thread.is_alive():
                watcher_thread.join(timeout=0.2)


# ---------------------------------------------------------------------------
# TUI Status Bar Renderer
# ---------------------------------------------------------------------------

class TUIStatusBar:
    """Renders a live status bar at the bottom of the terminal during PTY sessions."""

    def __init__(self, account_manager: MultiAccountManager):
        self.account_manager = account_manager
        self.active_account_idx = 0
        self.enabled = os.environ.get("AGY_STATUSBAR", "1") != "0"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled or not sys.stdout.isatty():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.draw()
            except Exception:
                pass
            time.sleep(1.5)

    def draw(self) -> None:
        if not sys.stdout.isatty():
            return
        cols, lines = shutil.get_terminal_size()
        if cols < 30 or lines < 5:
            return

        now = time.time()
        idx = self.active_account_idx
        num_acc = max(1, len(self.account_manager.accounts))
        acc = self.account_manager.accounts[idx] if idx < len(self.account_manager.accounts) else None

        # Quota part
        if acc and not acc.is_available(now):
            rem = format_remaining_time(acc.remaining_cooldown(now))
            quota_part = f"Quota: ⚠ 0% (in {rem})"
        elif acc:
            pct = acc.remaining_quota_pct(now)
            quota_part = f"Quota: ● {pct:.0f}%"
        else:
            quota_part = "Quota: ● 100%"

        # Context fill part
        used_tok, max_tok, fill_pct = get_latest_context_fill()
        if used_tok > 0:
            used_k = f"{used_tok / 1000:.1f}k"
            max_m = f"{max_tok / 1_000_000:.1f}M"
            ctx_part = f"Context: {used_k}/{max_m} ({fill_pct:.1f}%)"
        else:
            ctx_part = "Context: --"

        acc_name = acc.name if acc else "Default"
        bar_text = f" AGY | Acc {idx + 1}/{num_acc} ({acc_name}) | {quota_part} | {ctx_part} "
        padded_bar = bar_text.ljust(cols)[:cols]

        # ANSI DEC save/restore cursor
        seq = f"\0337\033[{lines};1H\033[7;36m{padded_bar}\033[0m\0338"
        try:
            sys.stdout.write(seq)
            sys.stdout.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Supervisor Decision Logic (Most-Remaining Selection & Debounce)
# ---------------------------------------------------------------------------

def _apply_continue(argv: List[str]) -> List[str]:
    """Ensure relaunch args contain 'continue'."""
    res = list(argv)
    if "continue" not in res and not any(a.startswith("-p") for a in res):
        res.append("continue")
    return res


def _handle_quota_event_smart(
    guard: QuotaGuard,
    duration: float,
    source_ts: Optional[float],
    account_manager: MultiAccountManager,
    current_account_index: int,
    is_pty: bool = False,
    master_fd: Optional[int] = None,
) -> Tuple[str, int]:
    """Smart quota handler with Most-Remaining account selection.

    Returns (action, next_index):
      - 'rotate-account': immediately rotate to next account with quota.
      - 'wait-complete': waited until replenished, continue.
      - 'wait-aborted': user pressed ESC or sent signal.
    """
    now = time.time()

    # Debounce check
    if now - guard.last_quota_trigger_ts < guard.debounce_window_seconds and guard.last_quota_duration > 0:
        # Same burst: update remaining duration but do not duplicate cycle
        guard.last_quota_duration = duration
        return ("debounce-ignore", current_account_index)

    guard.last_quota_trigger_ts = now
    guard.last_quota_duration = duration

    # 1. Mark current account exhausted
    account_manager.mark_exhausted(current_account_index, duration, source_ts)

    # 2. Select the account with the MOST remaining quota
    best_index, wait_needed, reason = account_manager.select_best_account(exclude_index=current_account_index)

    # If an alternative account is available right now with 0s wait: rotate immediately!
    if wait_needed <= 0 and best_index != current_account_index:
        banner = (
            f"\n\r[AGY Quota Guard] Quota erschöpft für Account {current_account_index + 1} "
            f"-> Wechsle zu Account {best_index + 1} ({account_manager.accounts[best_index].name}, {reason}) ...\n\r"
        )
        try:
            sys.stdout.write(banner)
            sys.stdout.flush()
        except Exception:
            pass
        guard.clear_anchor()
        return ("rotate-account", best_index)

    # 3. All accounts exhausted (or single account): wait for best account's cooldown
    remaining, anchor = guard._resolved_duration(wait_needed if wait_needed > 0 else duration, source_ts)
    banner = (
        f"\n\r[AGY Quota Guard] Alle {len(account_manager.accounts)} Accounts erschöpft. "
        f"Günstigster Account {best_index + 1} ({account_manager.accounts[best_index].name}) "
        f"resettet in {format_remaining_time(remaining)} — warte (ESC = abbrechen).\n\r"
    )
    try:
        sys.stdout.write(banner)
        sys.stdout.flush()
    except Exception:
        pass

    if remaining <= 0:
        guard.clear_anchor()
        return ("rotate-account" if best_index != current_account_index else "wait-complete", best_index)

    def _passthrough(data: bytes) -> None:
        if is_pty and master_fd is not None:
            try:
                os.write(master_fd, data)
            except OSError:
                pass

    completed = guard.wait_until_replenished(remaining, anchor_ts=anchor, passthrough=_passthrough)
    if not completed:
        if is_pty and master_fd is not None:
            try:
                os.write(master_fd, b"\x1b")
            except OSError:
                pass
        return ("wait-aborted", current_account_index)

    return ("rotate-account" if best_index != current_account_index else "wait-complete", best_index)


def _handle_quota_event(
    guard: "QuotaGuard",
    duration: float,
    source_ts: Optional[float],
    account_prefixes: List[str],
    account_index: int,
) -> Tuple[str, int]:
    """Quota decision for sequential accounts or headless path."""
    remaining, anchor = guard._resolved_duration(duration, source_ts)

    if account_prefixes:
        if account_index + 1 < len(account_prefixes):
            print(
                "[AGY Quota Guard] Quota erschöpft — wechsle automatisch zum nächsten Account "
                f"({account_index + 2}/{len(account_prefixes)}) ...",
                flush=True,
            )
            guard.clear_anchor()
            return ("rotate-account", account_index + 1)
        print(
            f"[AGY Quota Guard] Alle {len(account_prefixes)} Accounts erschöpft — "
            f"warte {format_remaining_time(remaining)} (ESC = abbrechen).",
            flush=True,
        )

    if remaining <= 0:
        guard.clear_anchor()
        return ("wait-complete", account_index)

    completed = guard.wait_until_replenished(remaining, anchor_ts=anchor)
    return ("wait-complete" if completed else "wait-aborted", account_index)


def _handle_quota_event_p(
    guard: "QuotaGuard",
    duration: float,
    source_ts: Optional[float],
    master_fd: int,
    proc,
    account_prefixes: List[str],
    account_index: int,
) -> Tuple[str, int]:
    """Quota decision inside live PTY loop with sequential prefixes."""
    remaining, anchor = guard._resolved_duration(duration, source_ts)

    if account_prefixes:
        if account_index + 1 < len(account_prefixes):
            banner = (
                "\n\r[AGY Quota Guard] Quota erschöpft — wechsle automatisch zum nächsten Account "
                f"({account_index + 2}/{len(account_prefixes)}) ...\n\r"
            )
            try:
                sys.stdout.write(banner)
                sys.stdout.flush()
            except Exception:
                pass
            guard.clear_anchor()
            return ("rotate-account", account_index + 1)
        banner = (
            f"\n\r[AGY Quota Guard] Alle {len(account_prefixes)} Accounts erschöpft — "
            f"warte {format_remaining_time(remaining)} (ESC = abbrechen).\n\r"
        )
        try:
            sys.stdout.write(banner)
            sys.stdout.flush()
        except Exception:
            pass

    if remaining <= 0:
        guard.clear_anchor()
        return ("wait-complete", account_index)

    def _passthrough(data: bytes) -> None:
        try:
            os.write(master_fd, data)
        except OSError:
            pass

    completed = guard.wait_until_replenished(remaining, anchor_ts=anchor, passthrough=_passthrough)
    if not completed:
        try:
            os.write(master_fd, b"\x1b")
        except OSError:
            pass
        return ("wait-aborted", account_index)
    return ("wait-complete", account_index)


# ---------------------------------------------------------------------------
# Supervised Execution Loop
# ---------------------------------------------------------------------------

def _launch_once(
    argv: List[str],
    account_index: int,
    account_prefixes: List[str],
    account_manager: Optional[MultiAccountManager] = None,
) -> Tuple[int, Optional[Tuple[float, Optional[float]]]]:
    """Execute command once and return (return_code, quota_event)."""
    import subprocess
    cmd = list(argv)
    env = dict(os.environ)

    if account_manager and 0 <= account_index < len(account_manager.accounts):
        acc = account_manager.accounts[account_index]
        if acc.cli_prefix:
            cmd.extend(acc.cli_prefix)
        if acc.env:
            env.update(acc.env)
    elif account_prefixes and 0 <= account_index < len(account_prefixes):
        cmd.extend(resolve_cli_prefix(account_prefixes[account_index]))

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    captured = []
    quota_event = None

    guard = QuotaGuard()
    if proc.stdout:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            captured.append(line)
            res = guard.scan_for_quota_error(line)
            if res is not None:
                quota_event = res  # Newer line overwrites older line (Last error wins!)

    ret = proc.wait()
    if quota_event is None and captured:
        quota_event = guard.scan_for_quota_error("".join(captured))

    return ret, quota_event


def run_supervised(
    argv: List[str],
    buffer_seconds: float = 3.0,
    guard: Optional[QuotaGuard] = None,
    accounts: Optional[List[str]] = None,
) -> int:
    """Supervise AGY execution with Multi-Account failover and TUI Statusbar."""
    if guard is None:
        guard = QuotaGuard(buffer_seconds=buffer_seconds)

    if accounts:
        state_dir = os.path.dirname(os.path.abspath(guard.state.path))
        mgr = MultiAccountManager(
            state_path=os.path.join(state_dir, "account_quotas.json"),
            explicit_prefixes=accounts,
        )
        guard.account_manager = mgr
    else:
        mgr = guard.account_manager
    account_index = 0
    max_headless_relaunches = int(os.environ.get("AGY_QUOTA_MAX_RELAUNCHES", "25"))
    headless_relaunches = 0

    current_log = find_latest_active_log_file()
    if current_log and os.path.exists(current_log):
        guard.last_log_offset = os.path.getsize(current_log)

    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

    # Initial check for persisted anchor
    persisted = guard.state.read()
    if persisted and persisted.get("target_time"):
        remaining = float(persisted["target_time"]) - time.time()
        if remaining > 0:
            if not guard.wait_until_replenished(remaining, anchor_ts=time.time(), abortable=True):
                return 130

    while True:
        if not is_interactive:
            # Headless execution
            return_code, quota_event = _launch_once(argv, account_index, [], account_manager=mgr)
            if quota_event is not None:
                dur, source_ts = quota_event
                action, next_index = _handle_quota_event_smart(guard, dur, source_ts, mgr, account_index, is_pty=False)
                if action == "wait-aborted":
                    return 130
                if action == "rotate-account":
                    account_index = next_index
                    argv = _apply_continue(argv)
                    continue
                if action == "wait-complete":
                    headless_relaunches += 1
                    if headless_relaunches > max_headless_relaunches:
                        return return_code if return_code else 1
                    argv = _apply_continue(argv)
                    continue

            headless_relaunches += 1
            if return_code == 0 or headless_relaunches > max_headless_relaunches:
                return return_code
            argv = _apply_continue(argv)
            continue

        # Interactive PTY supervisor with TUI Status Bar
        import pty
        import tty
        import termios
        import subprocess

        master_fd, slave_fd = pty.openpty()
        old_term_settings = None

        def _pty_account_cmd() -> Tuple[List[str], Dict[str, str]]:
            cmd = list(argv)
            env = dict(os.environ)
            if 0 <= account_index < len(mgr.accounts):
                acc = mgr.accounts[account_index]
                if acc.cli_prefix:
                    cmd.extend(acc.cli_prefix)
                if acc.env:
                    env.update(acc.env)
            return cmd, env

        cmd, child_env = _pty_account_cmd()

        # Start live TUI status bar
        status_bar = TUIStatusBar(mgr)
        status_bar.active_account_idx = account_index
        status_bar.start()

        try:
            try:
                old_term_settings = termios.tcgetattr(sys.stdin.fileno())
            except Exception:
                pass

            if old_term_settings:
                tty.setraw(sys.stdin.fileno())

            proc = subprocess.Popen(
                cmd,
                env=child_env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid,
            )
            os.close(slave_fd)

            def sigwinch_handler(signum, frame):
                try:
                    import fcntl
                    import struct
                    s = struct.pack("HHHH", 0, 0, 0, 0)
                    size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, s)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
                    status_bar.draw()
                except Exception:
                    pass

            try:
                signal.signal(signal.SIGWINCH, sigwinch_handler)
                sigwinch_handler(None, None)
            except Exception:
                pass

            log_check_interval = 0.5
            last_log_check = time.time()
            rotate_requested = False
            restart_requested = False
            pending_quota_event = None

            while proc.poll() is None:
                rlist = [master_fd, sys.stdin.fileno()]
                r, _, _ = select.select(rlist, [], [], 0.1)

                if master_fd in r:
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            try:
                                sys.stdout.buffer.write(data)
                                sys.stdout.buffer.flush()
                            except BrokenPipeError:
                                break
                            text = data.decode("utf-8", errors="ignore")
                            result = guard.scan_for_quota_error(text)
                            if result is not None:
                                dur, source_ts = result
                                action, next_index = _handle_quota_event_smart(
                                    guard, dur, source_ts, mgr, account_index, is_pty=True, master_fd=master_fd
                                )
                                if action == "rotate-account":
                                    account_index = next_index
                                    status_bar.active_account_idx = account_index
                                    proc.terminate()
                                    rotate_requested = True
                                    break
                                if action == "wait-complete":
                                    if proc.poll() is None:
                                        os.write(master_fd, b"continue\r\n")
                                    else:
                                        restart_requested = True
                    except (OSError, IOError):
                        break

                if sys.stdin.fileno() in r:
                    try:
                        user_data = os.read(sys.stdin.fileno(), 1024)
                        if user_data:
                            os.write(master_fd, user_data)
                    except (OSError, IOError):
                        break

                now = time.time()
                if now - last_log_check >= log_check_interval:
                    last_log_check = now
                    result = guard.scan_for_quota_error()
                    if result is not None:
                        dur, source_ts = result
                        action, next_index = _handle_quota_event_smart(
                            guard, dur, source_ts, mgr, account_index, is_pty=True, master_fd=master_fd
                        )
                        if action == "rotate-account":
                            account_index = next_index
                            status_bar.active_account_idx = account_index
                            proc.terminate()
                            rotate_requested = True
                            break
                        if action == "wait-complete":
                            if proc.poll() is None:
                                os.write(master_fd, b"continue\r\n")
                            else:
                                restart_requested = True

            return_code = proc.wait()
            status_bar.stop()

            if rotate_requested or restart_requested:
                argv = _apply_continue(argv)
                continue

            return return_code

        finally:
            status_bar.stop()
            if old_term_settings:
                try:
                    termios.tcsetattr(
                        sys.stdin.fileno(),
                        termios.TIOCSADRAIN if hasattr(termios, "TIOCSADRAIN") else termios.TCSADRAIN,
                        old_term_settings,
                    )
                except Exception:
                    pass
            try:
                os.close(master_fd)
            except Exception:
                pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if os.environ.get("AGY_QUOTA_GUARD", "1") == "0":
        if not args:
            print("Usage: python3 -m agy_modules.infrastruktur.quota_guard [--quota-accounts <spec>] <cmd> [args...]")
            sys.exit(1)
        os.execvp(args[0], args)

    accounts: List[str] = []
    cmd_args: List[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--quota-accounts", "--accounts") and i + 1 < len(args):
            accounts = parse_accounts(args[i + 1])
            i += 2
        elif arg.startswith("--quota-accounts=") or arg.startswith("--accounts="):
            accounts = parse_accounts(arg.split("=", 1)[1])
            i += 1
        else:
            cmd_args.append(arg)
            i += 1

    if not cmd_args:
        print("Usage: python3 -m agy_modules.infrastruktur.quota_guard [--quota-accounts <spec>] <cmd> [args...]")
        sys.exit(1)

    sys.exit(run_supervised(cmd_args, accounts=accounts))
