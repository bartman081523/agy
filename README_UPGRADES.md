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

| Launcher | Default Active Modules |
|---|---|
| `bin/agy-infrastruktur` | `AGY_INFRASTRUCTURE=1` |
| `bin/agy-epistemic` | `AGY_EPISTEMIC=1` |
| `bin/agy-symbolic` | `AGY_SYMBOLIC=1` |
| `bin/agy-socratic` | `AGY_SOCRATIC=1` |
| `bin/agy-symcomp` | `AGY_SYMCOMP=1` |
| `bin/agy-symbolic-epistemic` | `AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1` |
| `bin/agy-full` | `AGY_INFRASTRUCTURE=1 AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1 AGY_SYMCOMP=1` |
| `bin/agy-modular` | Configurable on-the-fly via environment variables |

---

## 3. Environment Variable Toggles

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

## 4. Test Suite

Execute the comprehensive automated test suite across all 21 unit tests and 8 launchers:

```bash
tests/run_all_tests.sh
```

---

## 5. Claude Skills & MCP Integration

- **MCP Integration**: Configured in `~/.gemini/config/mcp_config.json` and `.agents/mcp_config.json`:
  - `claude-history`: `/run/media/julian/ML4/gemini-cli/claude-history-mcp/run_server.sh`
  - `rea`: `node /run/media/julian/ML6/rea/scripts/rea.mjs mcp`
- **Claude Skills**: All 59 Claude skills from `~/.claude/skills` (including `antigravity-export`, `gemini-export`, `scimind5-epistemic`, `phimind`, `opencode-session-read`) are loaded globally via `~/.gemini/config/skills.json` and symlinked at `~/.gemini/config/skills`.
