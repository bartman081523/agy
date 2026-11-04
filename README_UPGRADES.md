# Antigravity (AGY) Modular Upgrades & Reasoning Suites

Ported and synthesized via the **MetaOrchestratorMind** protocol from `gemini-cli`, `opencode` (oc-variants), `pi` (pi-variants), and `claude_code_symbolic`.
Aktueller Basisstand: **Upstream Antigravity CLI v1.5.0** (`c994f07`).

---

## 1. Branch Architecture

| Branch | Description | Key Ported Features |
|---|---|---|
| `branch/infrastruktur` | Core stability & context management | Session reselection, history persistence, audio/token guard, Quota Guard supervisor, MCP sync (`claude-history`, `rea`) |
| `branch/epistemic` | SciMind 5.0 Epistemic Humility | Incomplete Suggestion Protocol, Empirical Verification, Falsificationism (Via Negativa), Operational Robustness |
| `branch/symbolic` | ℧ Cognito-Constructs | `<construct>` grammar, formal claims & invariants, `[ESCALATE]` contradiction decider, channel guards |
| `branch/sokratischer-dialog` | Classical Dialectic Moves | Sokratische Aporie (impasse recognition), Platonischer Ideal-Check (invariance), Aristotelische Self-Critique (telos) |
| `branch/symcomp` | Converged Construct Compaction | Token-efficient auto-compaction, ℧-converged reducer, "Next:" follow-up prime |
| `branch/symbolic-epistemic` | Integrated Reasoning Suite | Combined Epistemic + Symbolic + Socratic modules |
| `branch/full-stack` | Unified Master Architecture | All modules integrated, active by default, modular via environment flags, merged with Upstream 1.5.0 |

---

## 2. Launchers (`bin/` und `~/.local/bin/`)

Alle Launcher wrappen die Basis-Binary `agy` (`/home/julian/.local/bin/agy`, Version **1.5.0**) und sind als System-Symlinks in `~/.local/bin/` verfügbar:

| Launcher | Default Active Modules | YOLO Support | Quota Guard Support |
|---|---|---|---|
| `agy-infrastruktur` | `AGY_INFRASTRUCTURE=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-epistemic` | `AGY_EPISTEMIC=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-symbolic` | `AGY_SYMBOLIC=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-socratic` | `AGY_SOCRATIC=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-symcomp` | `AGY_SYMCOMP=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-symbolic-epistemic` | `AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-full` | `AGY_INFRASTRUCTURE=1 AGY_EPISTEMIC=1 AGY_SYMBOLIC=1 AGY_SOCRATIC=1 AGY_SYMCOMP=1` | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |
| `agy-modular` | Configurable on-the-fly via CLI / ENV | Yes (`--yolo`, `-y`) | Yes (`--quota-guard`) |

---

## 3. Quota Guard Supervisor (`agy_modules/infrastruktur/quota_guard.py`)

Überwacht AGY-Sitzungen in Echtzeit auf Gemini-Quota-Limits (`RESOURCE_EXHAUSTED` / HTTP 429):

1. **Last-Error Debounce:**
   - Bei mehrfachen Quota-Meldungen in schneller Folge (Streaming-Bursts, Wiederholungen, Log-Replays) wird **nur die letzte/aktuellste Meldung** ausgewertet.
   - Ein Debounce-Fenster (3 Sekunden) verhindert doppelte Relaunch- oder Rotationsschleifen.
2. **Multi-Account-Pool (mindestens 7 Accounts):**
   - Verwaltet bis zu 7 (oder beliebig viele $N$) Accounts über `~/.gemini/antigravity-cli/accounts.json` oder CLI-Flag `--quota-accounts`.
   - Speichert CLI-Präfixe, Umgebungsvariablen (`GEMINI_API_KEY`, Tokens etc.) und Account-Namen.
3. **Reale Quota-Messung & Most-Remaining-Auswahl:**
   - Misst die Rest-Quote pro Account anhand von Token-Verbrauch im Zeitfenster und Cooldown-Deadlines.
   - **Auswahlregel:** Läuft ein Account leer, wählt der Guard automatisch den Account aus, der aktuell **noch am meisten Quote hat** (höchste verbleibende Prozentzahl).
   - Sind alle Accounts erschöpft, ermittelt der Guard den Account mit dem **frühesten Reset-Zeitpunkt** und wartet gezielt nur dieses Minimum ab.
4. **TUI Live-Statuszeile:**
   - In interaktiven TUI-Sitzungen (PTY) blendet der Supervisor am unteren Bildschirmrand eine dezente Statuszeile ein:
     ```
     AGY | Acc 1/7 (Work) | Quota: ● 95% | Context: 153.2k/1.0M (14.6%)
     ```
   - Zeigt den aktuellen Account, die gemessene Quota % und den exakten Context-Fill (Tokens im 1M-Fenster).
   - Kann bei Bedarf via `export AGY_STATUSBAR=0` abgeschaltet werden.
5. **ESC-Interrupt:**
   - Drücken von `ESC` während einer Wartezeit bricht das Warten sofort ab und übergibt die Kontrolle direkt an den Nutzer.

```bash
# Aufruf mit Quota Guard:
agy-full --quota-guard

# Aufruf mit Multi-Account-Pool:
agy-full --quota-guard --quota-accounts=~/.gemini/antigravity-cli/accounts.json

# Zusammen mit YOLO:
agy-full --yolo --quota-guard
```

---

## 4. Antigravity History MCP Server (`antigravity-history-mcp/`)

Ein vollständiger, FastMCP-basierter History-Server für Antigravity-Unterhaltungen mit voller Parität zu `claude-history-mcp`:
- **Pfad:** `/run/media/julian/ML6/agy/antigravity-history-mcp`
- **Tools (22 Tools):**
  - Session-Kontext: `get_context`, `get_session_meta`
  - Turns & Suche: `search_history`, `get_turn`, `get_turns` (inkl. Thinking/CoT)
  - Patches & Code: `search_patches`, `get_patch`, `get_patches`, `get_file_version`, `get_file_history`, `list_files_in_session`
  - Tasks & Artefakte: `list_tasks`, `list_subagents`, `search_plans`, `get_plan`, `list_artifacts`, `get_artifact`
  - Customizations: `list_skills`, `search_skills`, `get_skill`, `list_rules`, `get_rule`
- **Registriert in:**
  - Antigravity: `~/.gemini/config/mcp_config.json`
  - Claude Code: `~/.claude.json`

---

## 5. Validierung & Test-Matrix

```bash
# Gesamte Testsuite (55 Tests + Matrix):
./tests/run_all_tests.sh

# MCP-History Tests (14 Tests):
cd antigravity-history-mcp && ./venv/bin/python3 -m unittest discover -s tests
```
