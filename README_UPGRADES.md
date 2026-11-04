# Antigravity (AGY) Modular Upgrades & Reasoning Suites

Ported and synthesized via the **MetaOrchestratorMind** protocol from `gemini-cli`, `opencode` (oc-variants), `pi` (pi-variants), and `claude_code_symbolic`.

---

## 1. Branch Architecture

| Branch | Description | Key Ported Features |
|---|---|---|
| `branch/infrastruktur` | Core stability & context management | Session reselection, history persistence, audio/token guard, MCP sync (`claude-history`, `rea`) |
| `branch/epistemic` | SciMind 5.0 Epistemic Humility | Incomplete Suggestion Protocol, Empirical Verification, Falsificationism (Via Negativa), Operational Robustness |
| `branch/symbolic` | ℧ Cognito-Constructs | `<construct>` grammar, formal claims & invariants, `[ESCALATE]` contradiction decider, channel guards |
| `branch/sokratischer-dialog` | Classical Dialectic Moves | Sokratische Aporie (impasse recognition), Platonischer Ideal-Check (invariance), Aristotelische Self-Critique (telos) |
| `branch/symcomp` | Converged Construct Compaction | Token-efficient auto-compaction, ℧-converged reducer, "Next:" follow-up prime |
| `branch/symbolic-epistemic` | Integrated Reasoning Suite | Combined Epistemic + Symbolic + Socratic modules |
| `branch/full-stack` | Unified Master Architecture | All modules integrated, active by default, modular via environment flags |

---

## 2. Launchers (`bin/`)

All launchers wrap the base `agy` binary (`/home/julian/.local/bin/agy`) and forward all arguments (`"$@"`):

| Launcher | Default Active Modules | YOLO Support |
|---|---|---|
| `bin/agy-infrastruktur` | `AGY_INFRASTRUCTURE=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-epistemic` | `AGY_EPISTEMIC=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-symbolic` | `AGY_SYMBOLIC=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-socratic` | `AGY_SOCRATIC=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-symcomp` | `AGY_SYMCOMP=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-symbolic-epistemic` | `AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-full` | `AGY_INFRASTRUCTURE=1 AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1 AGY_SYMCOMP=1` | Yes (`--yolo`, `-y`) |
| `bin/agy-modular` | Configurable on-the-fly via environment variables | Yes (`--yolo`, `-y`) |

---

## 3. YOLO Mode (`--yolo` / `-y` / `AGY_YOLO=1`)

Like in `gemini-cli`, the `--yolo` flag enables full autonomous execution by automatically translating to AGY's `--dangerously-skip-permissions` flag:

```bash
# Direct CLI flag:
bin/agy-full --yolo -p "Run task without confirmation prompts"

# Short alias:
bin/agy-full -y -p "Fix the code"

# Environment toggle:
AGY_YOLO=1 bin/agy-modular -p "Execute non-interactively"
```

---

## 4. Environment Variable Toggles

Every module can be individually activated (`1`) or disabled (`0`):

```bash
# Example: Full-stack with symbolic constructs disabled
AGY_SYMBOLIC=0 bin/agy-full

# Example: Custom modular invocation
AGY_EPISTEMIC=1 AGY_SYMCOMP=1 bin/agy-modular

# Inspect active status
bin/agy-modular --status

# List all variants
bin/agy-modular --list-variants
```

---

## 5. Test Suite

Execute the comprehensive automated test suite (including 25 unit tests covering all 32 module combinations and launcher targets):

```bash
tests/run_all_tests.sh
```

---

## 6. Claude Skills & MCP Integration

- **MCP Integration**: Configured in `~/.gemini/config/mcp_config.json` and `.agents/mcp_config.json`:
  - `claude-history`: `/run/media/julian/ML4/gemini-cli/claude-history-mcp/run_server.sh`
  - `rea`: `node /run/media/julian/ML6/rea/scripts/rea.mjs mcp`
- **Claude Skills**: All 59 Claude skills from `~/.claude/skills` (including `antigravity-export`, `gemini-export`, `scimind5-epistemic`, `phimind`, `opencode-session-read`) are loaded globally via `~/.gemini/config/skills.json` and symlinked at `~/.gemini/config/skills`.

---

## 7. Quota Guard & Seamless Auto-Resume (`AGY_QUOTA_GUARD=1`)

When Gemini API quota is exhausted (`RESOURCE_EXHAUSTED (code 429)`), AGY receives server messages detailing the exact replenishment time:
- Example: `Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 31m39s.`
- Example: `Individual quota reached... Resets in 10m15s.`

Instead of repeated wasteful polling or arbitrary exponential backoff, **Quota Guard** (`agy_modules/infrastruktur/quota_guard.py`):
1. **Parses Exact Reset Duration**: Extracts the precise remaining time (hours, minutes, seconds, or ISO timestamps) across English and German server formats.
2. **Timestamp Anchoring (Last Message)**: The countdown is anchored to the timestamp of the quota message — by preference the glog timestamp of the log line (`I1023 04:55:59.240652`), otherwise the `created_at` of the last quota message in `transcript.jsonl` of the conversation (newest sessions are scanned when the session id is unknown). A restart of agy or a reload of the conversation therefore **resumes** the countdown instead of restarting it from scratch: `remaining = parsed duration − (now − message timestamp)`. The remaining deadline is additionally persisted in `~/.gemini/antigravity-cli/quota_anchor.json` (max. 24 h old) so even a restart of the supervisor itself resumes correctly; the anchor reference is printed as `(Anker: letzte Nachricht HH:MM:SS)`.
3. **Displays TUI Status & Countdown**: Informs the user directly in the terminal with a real-time countdown to quota replenishment (`Pausiere automatisch bis HH:MM:SS`).
4. **ESC-Interruptible Wait ("quasi as a command")**: While waiting, the guard watches the terminal: pressing **ESC** aborts the wait immediately — the ESC byte is forwarded to the supervised TUI, where it is treated like a normal command (no `continue` is injected, the user regains full control). Any other keys typed during the wait are relayed to the TUI. In headless (non-TTY) runs, `SIGINT`/`SIGTERM` act as the equivalent command and cancel the wait.
5. **Seamless Auto-Resume**:
   - **Interactive TUI Mode**: Injects `continue\r\n` into the running session PTY as soon as the quota is replenished, automatically continuing programming without requiring manual user intervention.
   - **Process Relaunch Mode**: Relaunches the session preserving `--conversation <id>` or `--continue` and all flags (including `--yolo`).
   - **Spin Protection**: Headless relaunches are bounded (`AGY_QUOTA_MAX_RELAUNCHES`, default 25) and a fresh child that reports a large reset duration just after a completed wait is held instead of relaunched — no hot loops in environments where the child cannot make progress.
6. **Multi-Account Auto-Failover** (`--quota-accounts`): List one or more account CLI prefixes; on quota exhaustion the guard automatically relaunches with the next account, and only waits once **all** accounts are exhausted. Account resets clear the persisted anchor. Accepted specs: inline comma list (`--quota-accounts '--account-work,--account-private'`), JSON file (`[["--account-work"], ["--account-private","-p"]]`), or text file (one prefix per line). Also settable via `AGY_QUOTA_ACCOUNTS`. Wired into `bin/agy-full`, `bin/agy-modular`, and `bin/agy-infrastruktur`; specifying any accounts implies `--quota-guard`. Never on by default: without the flag the guard runs without account failover.
7. **Configuration**: Default is **OFF** — nothing is activated until requested. `bin/agy-full --quota-guard` (optionally `--quota-accounts <spec>`) activates the guard; the environment variable `AGY_QUOTA_GUARD=1` activates it permanently. There is deliberately no `--no-quota-guard`: the guard only exists when its parameter is passed.
8. **Testing**: Verified with mock quota limits in `tests/test_quota_guard.py` covering timestamp anchoring (glog + transcript + persisted anchor), ESC abort with byte passthrough, SIGINT abort, account rotation, and both PTY injection and process restart.

