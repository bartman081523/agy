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

Alle Launcher wrappen die Basis-Binary `agy` (`~/.local/bin/agy`, Version **1.5.0**) und sind als System-Symlinks in `~/.local/bin/` verfügbar:

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
4. **TUI Live-Statuszeile (Native `statusLine` Standardmethode & `--tui-status`):**
   - Kann bei Bedarf über den dedizierten Parameter `--tui-status` (oder `--status-bar`) aktiviert werden:
     ```bash
     agy-full --tui-status
     agy-full --quota-guard --quota-accounts --tui-status
     ```
   - **Vollständige `Ctrl+O`-Kompatibilität:** Verwendet Antigravitys native Bubbletea-`statusLine`-Engine (`stack_with_default: true`), wodurch `Ctrl+O` (Ausklappen/Einklappen der Viewports und Diffs) flüssig und ohne Cursor-Artefakte oder Hänger funktioniert.
   - Zeigt den aktuellen Account, Quota % und den exakten Context-Fill (Tokens im Fenster bzw. dynamic `--compress` Limit):
     ```
     AGY | Acc 1/7 (Work) | Quota: ● 95% | Context: 41.0k/128.0k (32.0%)
     ```
   - **Permanente Installation in settings.json:**
     ```bash
     agy-full --install-statusline    # Dauerhaft in ~/.gemini/antigravity-cli/settings.json einrichten
     agy-full --uninstall-statusline  # Wieder entfernen
     ```
5. **ESC-Interrupt & Sauberes Beenden:**
   - Drücken von `ESC` bricht das Warten sofort ab und beendet den Wrapper sauber mit Code 130.
   - Beendet sich `agy` regulär oder mit Fehler, beendet sich der Wrapper unverzüglich mit demselben Return-Code ohne Endlosschleifen.
   - Wartezeiten über 1 Stunde (`AGY_QUOTA_MAX_WAIT`) werden nicht ausgesessen, sondern sofort abgebrochen.
   - Veraltete Cooldown-Stände können jederzeit per `--reset-quota` zurückgesetzt werden.
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

## 4. Multi-Account `/login` & Google OAuth Upstream Integration

Verwaltet dynamisch beliebig viele Account-Slots ($N \ge 1$) ohne Beschränkung. Unterstützt flexible Modell-Provider analog zu OpenCode sowie die **exakte Google-OAuth-Methode der Upstream-Version**.

### Google OAuth Upstream-Methode
- **Identische OAuth-Credentials:** Nutzt dieselben OAuth-Client-Credentials wie die Upstream-AGY-Binary (synchronisiert über Secret Service).
- **Lokaler Loopback-Browser-Flow:** Startet einen lokalen HTTP-Server (`localhost:43123/auth/callback`), öffnet den Browser zur Google-Zustimmung und tauscht den Auth-Code gegen Token und Benutzer-E-Mail ein.
- **Keyring-Synchronisation (Linux Secret Service):** Tokens werden mit dem System-Keyring via `secret-tool lookup/store service gemini username antigravity` synchronisiert, sodass Antigravity direkt und ohne erneutes Anmelden den aktiven Account nutzt.
- **Automatischer Refresh:** Abgelaufene OAuth-Access-Tokens werden bei Aktivierung oder vor API-Aufrufen automatisch über Google OAuth erneuert.

### Provider-Unterstützung
- **Google Gemini** (`google`): Google OAuth 2.0 (Browser-Sign-In wie Upstream AGY) oder `GEMINI_API_KEY`, Modelle wie `gemini-2.5-pro`, `gemini-2.5-flash`.
- **Anthropic Claude** (`anthropic`): `ANTHROPIC_API_KEY`, Modelle wie `claude-3-7-sonnet-20250219`, `claude-3-5-sonnet`.
- **OpenAI** (`openai`): `OPENAI_API_KEY`, Modelle wie `gpt-4o`, `gpt-4o-mini`, `o3-mini`, `o1`.
- **DeepSeek** (`deepseek`): `DEEPSEEK_API_KEY`, Modelle wie `deepseek-coder`, `deepseek-chat`, `deepseek-reasoner`.
- **Ollama (Lokal)** (`ollama`): `OLLAMA_BASE_URL` (Standard: `http://localhost:11434`), z.B. `qwen2.5-coder:32b`, `llama3.3:70b`.
- **OpenRouter** (`openrouter`): `OPENROUTER_API_KEY`, Modelle wie `anthropic/claude-3.7-sonnet`.
- **Custom (OpenAI-kompatibel)** (`custom`): eigene Base-URL, API-Key und Modell.

### Interaktives Menü & CLI-Befehle
- **Terminal-Befehle:**
  ```bash
  login                      # Direkter Aufruf aus beliebigem Verzeichnis via ~/.local/bin/login
  agy-full --login           # Über den agy-full Launcher
  agy-full /login
  ```
- **Interaktiver Antigravity Prompt:**
  - Eingabe von `/login` innerhalb einer laufenden AGY-Sitzung expandiert automatisch den `/login`-Skill (`~/.claude/skills/login/SKILL.md`).
- **Nicht-interaktive CLI-Befehle:**
  ```bash
  # Accounts anzeigen (inklusive [ACTIVE] Marker und OAuth-Status):
  agy-full --list-accounts

  # Aktiven Google-Account aus dem Linux-Keyring importieren:
  agy-full --import-keyring

  # Neuen Google-Account per OAuth im Browser anmelden:
  agy-full --oauth-login

  # Aktiven Account wechseln:
  agy-full --switch-account 1

  # API-Key-Account hinzufügen (format: name:provider:key[:model[:base_url]]):
  agy-full --add-account "ClaudeDev:anthropic:sk-ant-xxx:claude-3-7-sonnet-20250219"

  # Account-Slot löschen (Index 1..N):
  agy-full --delete-account 2
  ```

---

## 5. Context Autocompression (`--compress <size>`)

Ermöglicht die granulare Konfiguration der Context-Window-Größe und des Autocompression-Schwellenwerts (in SymComp und im TUI-Statusbalken):
```bash
agy-full --compress 128k
agy-full --compress 64k --tui-status
agy-full --compress 256k
agy-full --compress 1M
```
Unterstützte Suffixe: `k` ($\times 1024$), `m` ($\times 1024^2$) oder reine Token-Zahlen. Die TUI-Statuszeile skaliert den Context-Fill prozentual und visuell passgenau auf das konfigurierte Autocompress-Fenster.

---

## 6. Launcher-Parameter & `--help` Übersicht

Alle Launcher (`agy-full`, `agy-modular`, `agy-infrastruktur`) akzeptieren dedizierte Parameter und bieten ein vollständiges Hilfemenü via `--help` oder `-h`:

```bash
agy-full --help
```

### Typische Aufrufe:
```bash
# Volle Suite mit TUI-Status, YOLO und 128k Compression:
agy-full --epistemic --symbolic --symcomp --tui-status --yolo --compress 128k

# Granulare Modulsteuerung in agy-modular:
agy-modular --epistemic --symbolic --tui-status --yolo

# Account-Verwaltung:
agy-full --list-accounts
agy-full --login
```

---

## 7. Antigravity History MCP Server (`antigravity-history-mcp/`)

Ein vollständiger, FastMCP-basierter History-Server für Antigravity-Unterhaltungen mit voller Parität zu `claude-history-mcp`:
- **Pfad:** `antigravity-history-mcp/`
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

## 8. Agnostische Claude & Antigravity Skills-Synchronisation (`skills_sync.py`)

Gewährleistet, dass alle Skills aus `~/.claude/skills/` (wie `/metaorchestratormind`, `/devmind`, `/autostart-meta-prompt-orchestrator` etc.) vollständig und agnostisch in Antigravity als native Skills und dynamische Slash-Commands funktionieren:

- **Automatischer Frontmatter-Healer:**
  - Erkennt fehlendes YAML-Frontmatter (z. B. bei `devmind`, `systemmodifymind`) und erzeugt valides Frontmatter mit Name und extrahierter Zweckbeschreibung.
  - Korrigiert YAML-Syntaxfehler (wie ungequotete Doppelpunkte in `description:`, z. B. bei `genealogy-osint`, `trihelixmetamixmind`, `autostart-meta-prompt-orchestrator`), wodurch Antigravitys Go-Parser (`skills.go:271`) keine `invalid frontmatter format`-Fehler mehr wirft.
  - Alle 65 Skills in `~/.claude/skills` sind 100% valide und werden von Antigravity geladen.
- **Nahtlose Workspace-Brücke:**
  - Beim Start von `agy-full`, `agy-modular` und `agy-infrastruktur` wird die Skills-Synchronisation automatisch ausgeführt.
  - In jedem aktiven Workspace wird `<cwd>/.agents/skills.json` verknüpft, sodass alle Claude-Skills auch außerhalb des Antigravity-Stammverzeichnisses (z. B. in `~/projects/my-project`) direkt per `/` aufrufbar sind.

---

## 9. Validierung & Test-Matrix

```bash
# Gesamte Testsuite (74 Tests + Matrix):
./tests/run_all_tests.sh

# MCP-History Tests (14 Tests):
cd antigravity-history-mcp && ./venv/bin/python3 -m unittest discover -s tests
```
